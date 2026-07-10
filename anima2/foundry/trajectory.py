"""Independent trajectory recorder (PHASE5.md item 1) — ports the *shape* of
v1 `../anima/foundry/kernel/trajectory.py`'s `TrajectorySummary` with its
signal source swapped, since anima2 never touches the wire (DESIGN.md §2,
"Brain ⊥ Body" — see that module's own docstring: it deliberately never
imports `anima/` so a mutator can't game its own ruler by editing the agent).

Two independent channels feed one `TrajectorySummary`, exactly per PHASE5.md's
own "Honest note on how independent 'independent' is":

**Channel (a) — load-bearing.** `TrajectoryRecorder.start()`/`.finish()` issue
`[Get` reads over a **separate** `GmControl` connection (never the measured
agent's own body) at window start and end — the same command family
`control.py::stage()` already uses. The *server*, not the agent, reports these
numbers, so this is genuinely independent regardless of process boundary: the
measured agent's own code has no write path to what a different connection
asks the server. Populates `skills` (gain), `gold_start`/`gold_end`, and
`alive_start`/`alive_end`.

**Channel (b) — corroborating only.** `TappedBody` wraps the `Body` the
measured agent actually drives and passively records every `Observation` that
flows back and every `Action` that flows out, without ever touching the
agent's own reward computation (`Agent.tick()` behaves byte-for-byte the same
whether its body is tapped or not — see that class's own docstring). In one
Python interpreter this is protected only by the static import-graph guard
(`tests/test_foundry_import_guard.py`), not true process isolation — treated
as corroboration for (a), never as the hard-independent signal (see
PHASE5.md's own note for the subprocess-isolation upgrade path this phase
does not require). Populates `items_into_pack`, `action_counts`,
`steps_confirmed`/`steps_denied`, `speech_sent`/`speech_recv`,
`damage_dealt`/`damage_taken` (see the class docstring — currently always 0,
an honestly-flagged gap, not silently faked), `positions`, and `hp_samples`.

**Adaptations from v1, stated plainly (not glossed over):**
 - v1 decodes ~15 raw packet types across a whole session and discovers every
   skill that moved. anima2's channel (a) can only ask the server for named
   properties (`[Get Skills.<Name>.Base`), so the caller must name which
   skills to track up front (`skill_names=`) rather than discovering them.
 - v1's `action_counts`/`steps_confirmed`/`steps_denied` come from decoding
   C->S packet ids via `uoconst.ACTION_GROUP` and S->C `ConfirmWalk`/
   `DenyWalk` replies. anima2 has no packet stream, so `TappedBody`
   classifies each outgoing `Action`'s own `.type` string into the same
   move/use/speech/attack/trade/skill groups (`_ACTION_GROUP` below, the
   anima2 analog of v1 `uoconst.ACTION_GROUP`) and infers confirm/deny for a
   `Walk` from whether the *next* Observation's position actually changed in
   response — the identical "did the tile change at all" proxy
   `anima2/skills/smelt.py::MineSmeltDeliver._walk_toward` already uses for
   its own stall detection — since there is no wire-level `ConfirmWalk`/
   `DenyWalk` reply to read here.
 - v1's `alive_fraction` integrates a continuous HP *packet* timeline. anima2's
   channel (a) alive-state is a start/end binary GM `[Get Hits` read (`None`/
   inaccessible defaults to "alive", matching v1's own "no evidence of death
   -> assume alive"); channel (b) additionally taps the agent's own
   `Observation.player.hits` every tick for a finer, corroborating fraction
   (`TrajectorySummary.alive_fraction`, ported dead-interval integration from
   v1) — but it can only ever refine an already-alive channel-(a) reading,
   never override a channel-(a) *dead* endpoint upward. `foundry/fitness.py`'s
   `compute_fitness(..., channel_b=False)` deliberately drops back to the
   coarse binary, which is the whole point of the live gate's channel-(a)-only
   recomputation (PHASE5.md item 1's live verification gate).
 - v1's `entities_seen`/`mobiles_seen` (environment census) has no analog here
   (nothing in `compute_fitness` consumes it yet — v1's own descriptor.py,
   not yet ported, is the only consumer); `unique_regions` is channel
   (b)-only, from the tapped `positions` trail.
 - `damage_dealt`/`damage_taken` stay in the shape for structural parity with
   v1 but are **not implemented** — anima2's `Observation` has no per-attack
   damage packet to tap (`P_DAMAGE` in v1), so a live combat evaluation would
   need to infer damage from HP deltas of a specific tracked target, not yet
   built. Always `0` here; `compute_fitness`'s `damage_rate` term is
   consequently always `0.0` too. Harmless for this item's own live gate
   (a mining scenario, no combat), and `_liveness`'s ">=2 distinct action
   groups" rule doesn't need combat to be represented.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..contract import Action, Observation, Walk
from . import uoconst

if TYPE_CHECKING:
    from ..control import GmControl

#: Reverse of `uoconst.SKILL_NAMES` — display name -> UO skill id, so a GM
#: `[Get Skills.<Name>.Base` reply (queried BY NAME — see the module
#: docstring) can be folded back into the same id-keyed `SkillStat` shape
#: v1's wire-parsed skills used (needed for `profession_skill_gains`).
_NAME_TO_ID: dict[str, int] = {name: sid for sid, name in uoconst.SKILL_NAMES.items()}

#: anima2 analog of v1 `uoconst.ACTION_GROUP` — keyed by this project's own
#: `Action.type` string (the Observation/Action contract, `contract.py`), not
#: a wire packet id (see module docstring). Mirrors v1's grouping intent:
#: `move` / `use` (any manipulation: pick up, drop, equip, gump/target/popup
#: replies, single-click) / `speech` / `attack` / `trade` / `skill`
#: (`CastSpell`, UO's "use skill/spell" family).
_ACTION_GROUP: dict[str, str] = {
    "Walk": "move", "WalkTo": "move",
    "Use": "use", "PickUp": "use", "Drop": "use", "Equip": "use", "Click": "use",
    "GumpResponse": "use", "PopupRequest": "use", "PopupSelect": "use",
    "TargetObject": "use", "TargetGround": "use",
    "Say": "speech",
    "Attack": "attack", "WarMode": "attack",
    "BuyItems": "trade", "SellItems": "trade",
    "CastSpell": "skill",
}


@dataclass
class SkillStat:
    """One tracked skill's window-start/window-end reading. Mirrors v1's own
    `SkillStat` shape exactly (`id`/`name`/`first`/`last`/`gain`)."""

    id: int
    name: str
    first: float
    last: float

    @property
    def gain(self) -> float:
        return max(0.0, self.last - self.first)


@dataclass
class TrajectorySummary:
    """Everything `foundry/fitness.py` needs, from the two channels above.
    Field names deliberately mirror v1's `TrajectorySummary` where the shape
    carries over (`skills`, `items_into_pack`, `action_counts`,
    `steps_confirmed`/`steps_denied`, `speech_sent`/`speech_recv`,
    `damage_dealt`/`damage_taken`, `positions`) so `fitness.py`'s ported
    formulas need no renaming; see the module docstring for what's new
    (`gold_start`/`gold_end`/`alive_start`/`alive_end`/`hp_samples`) and why.
    """

    subject_serial: int = 0
    start_ts: float = 0.0
    end_ts: float = 0.0

    # --- channel (a): GM [Get reads, window start + end — load-bearing -----
    skills: dict[int, SkillStat] = field(default_factory=dict)
    gold_start: int = 0
    gold_end: int = 0
    alive_start: bool = True
    alive_end: bool = True

    # --- channel (b): passive observation tap — corroborating only ---------
    items_into_pack: list[tuple[int, int, float]] = field(default_factory=list)  # (graphic, amount, ts)
    action_counts: dict[str, int] = field(default_factory=dict)
    steps_confirmed: int = 0
    steps_denied: int = 0
    speech_sent: int = 0
    speech_recv: int = 0
    damage_dealt: int = 0   # not implemented — see module docstring; always 0
    damage_taken: int = 0   # not implemented — see module docstring; always 0
    positions: list[tuple[float, int, int]] = field(default_factory=list)  # (ts, x, y)
    hp_samples: list[tuple[float, int]] = field(default_factory=list)  # (ts, hits)

    # ---- derived metrics (mirrors v1's own property shapes) --------------
    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)

    @property
    def duration_h(self) -> float:
        return self.duration_s / 3600.0

    @property
    def skill_gain_total(self) -> float:
        return sum(s.gain for s in self.skills.values())

    @property
    def gold_delta(self) -> int:
        return self.gold_end - self.gold_start

    @property
    def total_actions(self) -> int:
        return sum(self.action_counts.values())

    @property
    def unique_regions(self) -> int:
        # 8x8-tile region buckets, matching v1's own REGION_SHIFT == 3.
        return len({(x >> 3, y >> 3) for _, x, y in self.positions})

    def alive_fraction(self, *, channel_b: bool = True) -> float:
        """Channel (a) alone: binary — `1.0` iff `[Get Hits` read alive at
        BOTH window start and end, else `0.0`. Coarse but load-bearing: it
        can never be overridden *upward* by channel (b) (a channel-(a) dead
        endpoint always wins). `channel_b=True` (the default) additionally
        corroborates with the tapped `hp_samples` timeline — v1's own
        dead-interval integration, ported verbatim — but only when channel
        (a) says alive at both ends; it can only refine that reading
        (catching a mid-window death channel (a)'s two endpoints alone would
        miss), never contradict it. `channel_b=False` deliberately drops back
        to the coarse binary — see `foundry/fitness.py`'s own `channel_b`
        parameter, which is what PHASE5.md item 1's live gate's
        channel-(a)-only recomputation actually exercises.
        """
        coarse = 1.0 if (self.alive_start and self.alive_end) else 0.0
        if coarse == 0.0 or not channel_b or len(self.hp_samples) < 2 or self.duration_s <= 0:
            return coarse
        dead_s = 0.0
        prev_ts = self.start_ts
        prev_dead = False
        for ts, hits in self.hp_samples:
            if prev_dead:
                dead_s += max(0.0, ts - prev_ts)
            prev_ts = ts
            prev_dead = hits <= 0
        if prev_dead:
            dead_s += max(0.0, self.end_ts - prev_ts)
        return max(0.0, min(1.0, 1.0 - dead_s / self.duration_s))

    def profession_skill_gains(self) -> dict[str, float]:
        """Total skill gain per profession category (v1 FOUNDRY.md §4),
        via the ported `uoconst.SKILL_CATEGORY` table."""
        out: dict[str, float] = {}
        for s in self.skills.values():
            cat = uoconst.SKILL_CATEGORY.get(s.id)
            if cat and s.gain > 0:
                out[cat] = out.get(cat, 0.0) + s.gain
        return out


class TrajectoryRecorder:
    """Owns both channels for one eval window on one subject character.

    Usage (mirrors the `TappedBody` wiring `foundry/eval.py` will formalize
    in item 2; the live gate script wires it by hand):

        gm = GmControl.spawn(...)               # a SEPARATE connection —
                                                  # never the subject's own body
        rec = TrajectoryRecorder(gm, subject_serial, skill_names=("Mining",))
        rec.start()                              # channel (a): window-start [Get reads
        tapped = TappedBody(subject_ipc_body, rec)
        agent = Agent(body=tapped, ...)          # channel (b): every tick taps through
        for _ in range(window_ticks):
            agent.tick()
        summary = rec.finish()                   # channel (a): window-end [Get reads
    """

    def __init__(
        self, gm: "GmControl", subject_serial: int, *, skill_names: Sequence[str] = ("Mining",),
    ) -> None:
        self.gm = gm
        self.subject_serial = subject_serial
        self.skill_names = tuple(skill_names)
        self.summary = TrajectorySummary(subject_serial=subject_serial)
        self._owned_backpack: int | None = None
        self._pack_amounts: dict[int, int] = {}
        self._pending_walk_pos: tuple[int, int] | None = None

    # -- channel (a): GM [Get reads -----------------------------------------

    def start(self) -> None:
        """Read skills/gold/alive-state now, as the window's channel-(a)
        baseline. Call once, before the measured agent's first tick."""
        self.summary.start_ts = time.time()
        for name in self.skill_names:
            base = self._read_skill_base(name)
            sid = _NAME_TO_ID.get(name, -1)
            self.summary.skills[sid] = SkillStat(id=sid, name=name, first=base, last=base)
        self.summary.gold_start = self._read_gold()
        self.summary.alive_start = self._read_alive()

    def finish(self) -> TrajectorySummary:
        """Re-read skills/gold/alive-state as the window's channel-(a) end
        reading, and return the completed summary. Call once, after the
        measured agent's last tick."""
        self.summary.end_ts = time.time()
        for name in self.skill_names:
            sid = _NAME_TO_ID.get(name, -1)
            stat = self.summary.skills.get(sid)
            last = self._read_skill_base(name)
            if stat is None:
                self.summary.skills[sid] = SkillStat(id=sid, name=name, first=last, last=last)
            else:
                stat.last = last
        self.summary.gold_end = self._read_gold()
        self.summary.alive_end = self._read_alive()
        return self.summary

    def _read_skill_base(self, name: str) -> float:
        val = self.gm.get_property_value(f"Skills.{name}.Base", self.subject_serial)
        return val if isinstance(val, float) else 0.0

    def _read_gold(self) -> int:
        val = self.gm.get_property_value("TotalGold", self.subject_serial)
        return int(val) if isinstance(val, float) else 0

    def _read_alive(self) -> bool:
        # `None` (no reply / inaccessible property) defaults to alive,
        # matching v1's own "no evidence of death -> assume alive".
        val = self.gm.get_property_value("Hits", self.subject_serial)
        return not (isinstance(val, float) and val <= 0)

    # -- channel (b): passive observation/action tap -------------------------

    def tap_observation(self, obs: Observation, ts: float | None = None) -> None:
        """Fold one Observation into the buffer. Called by `TappedBody` after
        every real `observe()` — never touches the agent's own reward path.
        """
        ts = time.time() if ts is None else ts
        p = obs.player
        self.summary.positions.append((ts, p.pos.x, p.pos.y))
        self.summary.hp_samples.append((ts, p.hits))

        if self._owned_backpack is None:
            bp = next(
                (i for i in obs.items if i.layer == uoconst.LAYER_BACKPACK and i.container == p.serial),
                None,
            )
            if bp is not None:
                self._owned_backpack = bp.serial
        if self._owned_backpack is not None:
            for it in obs.items:
                if it.container != self._owned_backpack:
                    continue
                prev = self._pack_amounts.get(it.serial, 0)
                if it.amount > prev:
                    self.summary.items_into_pack.append((it.graphic, it.amount - prev, ts))
                self._pack_amounts[it.serial] = it.amount

        for j in obs.new_journal:
            if j.serial != p.serial and j.text:
                self.summary.speech_recv += 1

        if self._pending_walk_pos is not None:
            if (p.pos.x, p.pos.y) == self._pending_walk_pos:
                self.summary.steps_denied += 1
            else:
                self.summary.steps_confirmed += 1
            self._pending_walk_pos = None

    def tap_action(self, action: Action, pre_obs: Observation) -> None:
        """Fold one outgoing Action into the buffer, given the Observation
        the measured agent picked it from (`pre_obs` — used only to remember
        the pre-move position for a `Walk`'s confirm/deny inference on the
        *next* `tap_observation` call). Called by `TappedBody` right before
        forwarding the action to the real body.
        """
        group = _ACTION_GROUP.get(getattr(action, "type", ""))
        if group:
            self.summary.action_counts[group] = self.summary.action_counts.get(group, 0) + 1
        if isinstance(action, Walk):
            self._pending_walk_pos = (pre_obs.player.pos.x, pre_obs.player.pos.y)
        if getattr(action, "type", "") == "Say":
            self.summary.speech_sent += 1


class TappedBody:
    """Kernel-owned `Body` wrapper — the mechanism channel (b) taps through.

    Forwards every `observe()`/`act()` call to `inner` unchanged (the measured
    agent's behavior is byte-for-byte identical to running undecorated — same
    contract `live_trade.py::_RecordingBody` already establishes for its own,
    unrelated caching reason) while feeding both into a `TrajectoryRecorder`.
    `anima2/skills/*`, `curriculum.py`, `skill_tuning.py`, `cognition.py`, and
    `skill_library.py` never import `anima2.foundry` (the import-graph guard)
    — the measured agent has no write path to what gets recorded here.
    """

    def __init__(self, inner, recorder: TrajectoryRecorder) -> None:
        self._inner = inner
        self._recorder = recorder
        self._last_obs: Observation | None = None

    @property
    def connected(self) -> bool:
        return self._inner.connected

    def observe(self) -> Observation:
        obs = self._inner.observe()
        self._recorder.tap_observation(obs)
        self._last_obs = obs
        return obs

    def act(self, action: Action) -> None:
        if self._last_obs is not None:
            self._recorder.tap_action(action, self._last_obs)
        self._inner.act(action)
