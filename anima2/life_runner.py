"""LifeRunner — the harness a Life runs on, so profession N+1 inherits every lesson.

Five runners grew in `village.py` by copy-paste, and the copies drifted: each new one
shipped WITHOUT some piece of infrastructure its predecessor had already paid for live —
the want/admitted/ready telemetry (added after an artisan stalled invisibly), the leash
on BOTH agent memories (added after a carpenter wandered off its own supply), staged-NPC
readback (added after a Banker resolved to the Weaponsmith's serial), starter-gold
provenance (added after a carpenter banked 940 unearned gold and reported it as income).
The commit record calls this out plainly: "carrying lessons forward is the part I keep
skipping."

This module makes the lessons STRUCTURAL. `LifeSpec` is a dataclass whose load-bearing
fields have no defaults — a runner that forgets its profession key or staging function
does not quietly run without telemetry, it fails to construct. `LifeRunner.run()` owns
the sequence nobody should re-implement: spawn (with the monitor seat), login throttle,
GM staging via the spec, **provenance always** (starter gold deleted before any seed is
granted), leash applied to BOTH agent memories, the worker thread with budget scaling,
and the standard status line — mode, want/admitted/ready, hp, pack gold, banked gold
(read from the bank BOX, the way `live_bank_goal.py` proves a deposit), and the loud
rule-vs-gate disagreement line.

The helpers are exported separately so the older multi-agent runners (`run_supply_pair`,
`run_warrior_village`, `run_artisan_mage_village`) can share the same code paths without
being forced through the single-agent harness shape.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .capabilities import ready_capability_ids
from .contract import Observation
from .control import GmControl
from .ipc_body import ResilientIpcBody
from .live_common import GM_RELOGIN_COOLDOWN_S, fresh_suffix, login_throttle
from .obsview import banked_amount, pack_amount
from .obsview import owned_tool_readout, pack_serial  # noqa: F401 — re-exported, see below
from .persona import Persona
from .skills.base import SkillContext
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import SELL_REACH, _bank_reserve
from .warrior_life import WarriorLife

#: First HTTP port handed to `--monitor`; each agent gets the next one up. Loopback
#: only (the bridge hardcodes 127.0.0.1), so this never exposes a shard to the network.
MONITOR_PORT_BASE = 8801


def monitor_ports(enabled: bool, roles: list[str]) -> dict[str, int | None]:
    """One viewer port per role, or all `None` when monitoring is off.

    Each agent runs its own bridge process and therefore its own viewer — a shard
    allows exactly one session per character, so there is no way to watch several
    characters through one connection.
    """
    if not enabled:
        return {r: None for r in roles}
    return {r: MONITOR_PORT_BASE + i for i, r in enumerate(roles)}


# --- observation readbacks — now in `obsview.py`, re-exported here -------------------
#
# This section was headed "the ~15 copy-pasted `_pack` helpers, once". The count was
# right; the "once" was not. These four were canonical only for the RUNNERS — the five
# Life modules went on keeping twenty hand-written copies of the same readbacks, and
# drifted anyway: a distance clause that was never back-ported, a missing-backpack guard
# that was never forward-ported. `obsview.py` is the one definition now; see its module
# docstring for what each drift cost live.
#
# `pack_serial`, `pack_amount`, `banked_amount` and `owned_tool_readout` are RE-EXPORTED
# from the import above (hence its `noqa: F401` — `pack_serial` and `owned_tool_readout`
# are no longer used inside this module, only through it), because `village.py`,
# `live_tinker_bank_gate.py`, `live_urgent_bank_gate.py` and `tests/test_life_runner.py`
# all import them from HERE. Moving the definitions must not move anybody's imports.


# --- staging (verified, never assumed) -----------------------------------------------

class StagingError(RuntimeError):
    """A shop failed to stage, resolved to an already-placed NPC, or landed on a tile
    another shop occupies. Raised (not printed) by strict staging: a solo Life with a
    missing or aliased shop is a broken run, and half-verified staging is the same trap as
    unverified staging in better clothes."""


def stage_shops(gm: GmControl, *, z: int, anchor: tuple[int, int],
                spots: dict[str, tuple[str, tuple[int, int]]],
                exclude, strict: bool = True,
                placed: dict[int, str] | None = None,
                serials_out: dict | None = None) -> tuple[dict, set]:
    """Stage every shop in `spots` (`key -> (npc_name, (x, y))`), READ BACK where each
    actually landed, and verify identity, uniqueness and reach.

    Every clause here is a live-caught lesson:
    - excludes the NPCs already placed, not just the player — `find_mobile_near` returns
      the nearest mobile, and with shops a tile apart that was a Banker resolving to the
      Weaponsmith's own serial (a banker_spot pointing at a vendor with no bank box);
    - prints each shop's real distance from `anchor` and flags OUT OF REACH — an
      unreachable shop is outwardly indistinguishable from a broken planner;
    - two shops on one tile is an error, not a curiosity.

    Returns `(routes, npc_tiles)`. With `strict=False` failures print and leave the
    route unset (the older multi-agent runners' behavior); with `strict=True` they raise
    `StagingError`.

    `serials_out`, when given, is filled with `{(x, y): serial}` for every shop actually
    staged — the SHOP-IDENTITY PIN. Runners hand it to the agent as the `shop_serials`
    memory key, and the market skills' own resolver prefers it over its
    nearest-to-requested-spot guess. That guess is a coin flip exactly where it matters
    most: a vendor and a banker one tile either side of a requested spot are EQUIDISTANT
    from it, and the live urgent-band gate caught the tinker asking the Tinker NPC to
    open a bank box (two runs out of three; the third guessed right). Identity, not
    distance — this project's oldest lesson, applied one layer deeper than staging.
    """
    routes: dict = {}
    # `placed` may be shared across calls: a multi-anchor runner stages shops for two
    # agents, and a shop placed for one must never answer for the other's.
    placed = placed if placed is not None else {}
    exclude = set(exclude) if not isinstance(exclude, int) else {exclude}
    for key, (npc, (nx, ny)) in spots.items():
        mob = gm.stage_npc(npc, nx, ny, z, exclude={*exclude, *placed})
        if mob is None or mob.serial in placed:
            msg = (f"{npc}: FAILED to stage — {key} left unset" if mob is None else
                   f"{npc}: resolved to the {placed[mob.serial]} NPC — {key} left unset")
            if strict:
                raise StagingError(msg)
            print(f"  {msg}")
            continue
        placed[mob.serial] = key
        if serials_out is not None:
            serials_out[(mob.pos.x, mob.pos.y)] = mob.serial
        reach = max(abs(mob.pos.x - anchor[0]), abs(mob.pos.y - anchor[1]))
        routes[key] = [(mob.pos.x, mob.pos.y)]
        flag = "" if reach <= SELL_REACH else "  ** OUT OF REACH **"
        print(f"  {npc}: at ({mob.pos.x},{mob.pos.y}), {reach} from the stand{flag}")
        if strict and reach > SELL_REACH:
            raise StagingError(f"{npc} landed {reach} tiles from the stand — out of reach")
    tiles = [tuple(v[0]) for v in routes.values()]
    if len(set(tiles)) != len(tiles):
        msg = f"two shops share a tile: {routes}"
        if strict:
            raise StagingError(msg)
        print(f"  ** {msg} **")
    return routes, set(tiles)


def enforce_gold_provenance(gm: GmControl, body, serial: int) -> None:
    """Delete every coin in the character's pack, so all later gold is EARNED.

    A fresh ServUO account arrives with ~1000 gold; a carpenter once banked it and the
    readout reported 940 "earned" — an impressive headline that was false. Run this
    BEFORE granting any seed."""
    st = [body.observe() for _ in range(3)][-1]
    pack = next((i.serial for i in st.items
                 if i.layer == BACKPACK_LAYER and i.container == serial), None)
    for i in st.items:
        if i.container == pack and i.graphic == GOLD_GRAPHIC:
            gm.command_on("[Delete", i.serial)


# --- telemetry -----------------------------------------------------------------------

#: One letter per retirement reason, for the tally on the ~4s status line. Ordered:
#: the tally prints in THIS order, not alphabetically, so `4a/1g/1x` always reads
#: achieved-first regardless of which buckets a run happens to fill.
RETIREMENT_CODES = (
    ("achieved", "a"),
    ("giveup", "g"),
    ("expired", "x"),
    ("replaced", "r"),
    ("cancelled", "c"),
)


def retirement_reason(frame) -> str:
    """WHY one capability frame left the stack — the distinction no live log could make.

    This is the 2026-08-03 finding (C) in one function. `CapabilityGoalComplete` closes
    a frame through exactly two branches (`profession.py`): the ACHIEVEMENT branch,
    which returns SUCCESS, and — reached only after it — the give-up-ladder branch keyed
    on `cap_run_finished_goal_id`, which returns FAILURE. That second one is BOUND 1 of
    the exit-edge hold, and it is the bound no live run has ever evidenced: every
    low-age frame in the logs closed beside a successful sale or deposit, a ladderless
    `buy_iron` frame closed just as fast at age 4, and the status line shows only that
    the frame is gone. "Low max age" is not a give-up signature.

    NOTHING NEW IS WRITTEN to make this readable, and the function takes the frame
    ALONE. The distinction already exists in `frame.outcome`, stamped by
    `GoalStack._archive` on every retirement since the goal stack was written:
    `agent.py` maps SUCCESS → `GoalOutcome.SUCCESS` and FAILURE → `GoalOutcome.FAILURE`,
    and `expire_due` stamps EXPIRED (bound 2). Being a projection of durable, per-frame
    state is what lets a retirement that happens between two ~4s samples still be
    reported — and what makes the answer independent of WHEN it is read.

    THE MARKER IS NOT USABLE HERE, and that is a measured correction to the design this
    was built from. `cap_run_finished_goal_id` is a SINGLE memory slot that every later
    transaction overwrites, so testing `memory[...] == frame.id` only ever confirms the
    newest frame to have walked a ladder. Measured on a 2000-tick `CarpenterLife`
    (`retirement_reason` classified at retirement time versus the same history re-read
    at the end): 117 of 117 FAILURE closes are give-ups, and 116 of the 117 flip to a
    false "no ladder ran" when read afterwards. The claim that a stale value "can only
    equal an OLDER frame's id" is true and is exactly the problem — the error direction
    it produces is the one that ERASES bound-1 evidence.

    FAILURE is therefore reported as `giveup` outright, which is sound:

    * Attribution. On a `kind == "capability"` frame only `CapabilityGoalComplete` can
      consume the goal (`agent.py` finishes with FAILURE in exactly one place, from a
      `consumes_goal` terminal skill, and its capability allowlist admits no other
      `consumes_goal` type — `GoTo` additionally requires `goal.kind == "goto"`, and
      `CapabilityBoundSkill` does not inherit its inner skill's `consumes_goal`).
    * The residual. `step` also returns FAILURE from its `not can_run` fail-closed
      guard, which would not be a ladder. That guard is unreachable through the planner:
      `Agent._isolated_planner_context` deep-copies memory and observation, so the
      `can_run` that selected the skill and the `can_run` inside `step` read identical
      content in the same tick. It has never been constructed by any test or gate, and
      even if it were, the frame still closed UNACHIEVED rather than expiring — the same
      side of the only split this report exists to make.

    One ordering fact worth keeping: `SellItemCapability` and `BankGoldCapability` set
    the run-finished marker UNCONDITIONALLY at the end of the return leg, so a run that
    achieved AND walked home satisfies both of `can_run`'s branches. `can_run` answers
    on the achievement branch, and `GoalOutcome.SUCCESS` records that — so such a frame
    reads `achieved`, not `giveup`, with no tie-break needed here.
    """
    outcome = getattr(getattr(frame, "outcome", None), "value", None)
    if outcome == "success":
        return "achieved"
    if outcome == "failure":
        return "giveup"
    return outcome or "?"  # expired (bound 2) / replaced / cancelled


def frame_retirements(life, *, after_id: int = 0) -> tuple[tuple, ...]:
    """`(id, capability, age, budget, reason)` for every CAPABILITY frame retired since
    `after_id`, oldest first — a pure projection of the goal stack that OWNS the frames.

    That stack is `life.econ_agent`'s on a Life, and the object's OWN on anything else
    that has one. The fallback is not defensive tidiness, it is the whole
    `run_village --capability-goals` fleet: `_build_villager_agent` hands `_run_worker` a
    PLAIN `Agent` carrying a real `CapabilityPolicy`, and that agent's capability frames
    retire on `agent.goal_stack` — it has no `econ_agent` and never will. Measured
    2026-08-03 (review): a blacksmith `bank_gold` frame closed through the FSM give-up
    ladder (`goal_stack.current is None`, `history[-1].outcome is FAILURE`) and this
    function returned `()`, so bound-1 observability — the entire point of the report —
    was dead for exactly the fleet it was added for. The reason review caught it and the
    tests did not is that `tests/test_capabilities.py::_retire_life` wraps that agent in
    a `type("_Life", (), {"econ_agent": agent})()` shim: an attribute no production code
    gives it. New tests drive the unwrapped agent.

    The cursor is a frame ID, never a count: history is bounded (`history_limit=128`),
    so a count silently under-reports after an overflow, while ids are monotonic and
    `> after_id` is total. Ages are in ECON-AGENT ticks, the same clock `deadline_tick`
    and `telemetry_line`'s `@age` are counted in.

    Attribution is exact rather than assumed: on a `kind == "capability"` frame only
    `CapabilityGoalComplete` can consume the goal — see `retirement_reason`, which also
    carries the reason this reads NOTHING but the frame. Reading durable per-frame state
    is what lets a retirement that lands between two ~4s samples still be reported, and
    what keeps an EDGE reader (the alarm, which drains by id every tick) and a LEVEL
    reader (`retirement_tally`, re-derived from scratch) agreeing on every frame they
    can both still see; an earlier draft consulted agent memory and its two readers
    disagreed on 116 of 117 frames. What durability does NOT survive is history
    overflowing: past 128 frames the oldest are deleted, the edge reader has already
    banked them and the level reader can no longer see them, which is why
    `retirement_tally` says `>=` once the cap binds rather than pretending to a total.
    """
    econ = getattr(life, "econ_agent", None) or life
    try:
        out = []
        for frame in getattr(econ.goal_stack, "history", ()):
            if frame.id <= after_id or getattr(frame.goal, "kind", None) != "capability":
                continue
            age = (None if frame.finished_tick is None
                   else frame.finished_tick - frame.created_tick)
            budget = (None if frame.deadline_tick is None
                      else frame.deadline_tick - frame.created_tick)
            out.append((frame.id, frame.goal.params.get("capability"), age, budget,
                        retirement_reason(frame)))
        return tuple(out)
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        return ()


def _history_saturated(life) -> bool:
    """Has the goal stack this tally reads started DELETING its oldest frames?

    `GoalStack._archive` trims from the FRONT once `len(history) > history_limit`, so
    from that moment the tally is a rolling window over the newest 128 frames and its
    count is a floor, not a total. Measured (review, 2026-08-03) on the very harness
    `tests/test_life_frame_hold.py::test_the_reason_does_not_depend_on_when_it_is_read`
    uses — a `CarpenterLife` with a saw, four board-items and 5000g: 58 retirements at
    1000 ticks, 117 at 2000, 176 at 3000, 234 at 4000, with history pinned at 128 from
    econ tick 2182 on. So the docstring this replaced — "128 capability frames is far
    more than any run has produced" — was false at 3000 ticks, a window
    `warrior_life.py` and CLAUDE.md both describe as ordinary, where the unmarked tally
    reported 128 of 176 actual retirements: a silent 27% under-report of a field printed
    as a count.
    """
    try:
        stack = (getattr(life, "econ_agent", None) or life).goal_stack
        return len(stack.history) >= stack.history_limit
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        return False


def retirement_tally(life) -> str:
    """`retired=6:4a/1g/1x` — the LEVEL signal beside the per-tick edge alarm.

    The alarm in `village._run_worker` scrolls away between status blocks; this is what
    an operator joining late, or grepping the log afterwards, still sees.

    The count is retirements STILL IN bounded history, never a lifetime total, and it
    SAYS SO: once the 128-frame cap binds the field reads `retired>=128:...`. Without
    that marker the two readers of the same history disagree the moment it overflows —
    the per-tick edge alarm had already reported 176 retirements on the run where this
    line still said 128 — and a reader has no way to tell a run that retired 128 frames
    from one that retired 234. `>=` is the honest thing a bounded projection can say;
    the edge alarm remains the exact per-frame record.
    """
    rets = frame_retirements(life)
    if not rets:
        return "retired=0"
    counts: dict[str, int] = {}
    for _, _, _, _, reason in rets:
        counts[reason] = counts.get(reason, 0) + 1
    parts = [f"{counts.pop(name)}{code}" for name, code in RETIREMENT_CODES
             if name in counts]
    parts += [f"{n}{name}" for name, n in sorted(counts.items())]  # any unforeseen bucket
    at_least = ">=" if _history_saturated(life) else "="
    return f"retired{at_least}{len(rets)}:{'/'.join(parts)}"


def telemetry_line(life, profession: str, obs: Observation | None) -> str:
    """`want=<intent> admitted=<goal actually on the stack> ready=<gate verdicts>`.

    `want` alone is a trap: it is INTENT, and an unadmitted goal looks identical to a
    busy one from outside — that ambiguity cost three runs and one wrong root cause.

    `admitted` had the SAME trap on its own side, and it cost a fourth run: a frame is
    on the stack whether or not anybody is ticking it, so `admitted=sell_furniture`
    printed for 280 live ticks while the orchestrator had switched to hunt mode and
    nothing was executing that capability (2026-08-03; the fix is the exit-edge hold in
    `WarriorLife.tick`). So `admitted` now carries the frame's AGE, in ECON-AGENT ticks:
    the same clock `deadline_tick` is counted in, and the clock that STOPS the moment
    the frame stops being ticked. A frozen frame is exactly one whose age no longer
    moves while these lines keep printing every few seconds; a slow-but-progressing one
    climbs toward its budget. Three suffixes name the state outright rather than leaving
    it to be inferred:

      `!frozen`  — a live frame whose agent is not the one being ticked. Under the hold
                   this means death, or a frame the hold has RELEASED as overdue; it is
                   also the REGRESSION DETECTOR for the hold itself: if it is ever
                   bypassed or defeated by a new early return, the status line says so
                   in the field instead of requiring a reader to correlate `[hunt]`
                   with `admitted=X`. Nobody made that correlation on 2026-08-03, and
                   it was printed on every line.
      `+hold`    — the LEGITIMATE `want=None admitted=X` pairing the hold creates: the
                   rule stopped wanting it, the orchestrator is finishing it anyway.
      `!overdue` — the frame is past its own budget. Printed IN ADDITION to the other
                   two, because "the age exceeds the budget" is a comparison between two
                   numbers on the line and review-caught nobody makes it. `!frozen!overdue`
                   is the stale frame the hold released and no longer holds for.

    `retired=` closes the last gap those three leave: they all describe the frame that
    is HERE, and a frame that has already gone is simply absent — which is why bound 1
    of the exit-edge hold (the FSM's give-up ladder) could not be told from an ordinary
    successful sale on ANY 2026-08-03 log. See `retirement_reason`.
    """
    try:
        ready = ready_capability_ids(
            profession, SkillContext(obs=obs, persona=life.persona,
                                     memory=life.econ_agent.memory),
        ) if obs is not None else ()
        cur = life.econ_agent.goal_stack.current
        admitted = None
        if cur is not None:
            budget = ("" if cur.deadline_tick is None
                      else f"/{cur.deadline_tick - cur.created_tick}")
            admitted = (f"{cur.goal.params.get('capability')}"
                        f"@{life.econ_agent.ticks - cur.created_tick}{budget}")
            # `getattr` throughout: a Life is duck-typed here (tests and gates pass
            # stand-ins), and telemetry must never be the thing that raises.
            if getattr(life, "mode", "economy") != "economy":
                admitted += "!frozen"
            elif getattr(life, "holding_frame", False):
                admitted += "+hold"
            if getattr(life, "frame_overdue", False):
                admitted += "!overdue"
    except Exception:  # noqa: BLE001 — telemetry must never break the run
        ready, admitted = ("?",), "?"
    return (f"want={life.target_cap} admitted={admitted} ready={list(ready)} "
            f"{retirement_tally(life)}")


def hp_readout(obs: Observation | None) -> str:
    """A frozen agent looks the same as a busy one; a dead one explains everything."""
    if obs is None:
        return "?"
    p = obs.player
    return "DEAD" if p.dead else f"{p.hits}/{p.hits_max}"


def validate_knobs(knobs: dict[str, Any] | None, knob_names, *,
                   label: str) -> dict[str, Any]:
    """The ONE check that a caller's tuning dict contains only tuning KNOBS, and the
    single place the refusal is worded. Returns a COPY, splat-ready.

    Two separate failures, one check — both first found on the `LifeSpec` half of this
    channel, and both reachable from every other construction site the moment one grows
    a `knobs` argument, which is why this moved out of `LifeSpec.__post_init__`:

    The first is SILENT. A Life constructor's non-knob parameters are IDENTITY, and
    `profession` is the worst of them: `knobs={"profession": "mage"}` on a carpenter
    built a Life that staged, labelled and reported itself as a carpenter
    (`spec.profession` drives all three, including `telemetry_line`'s `ready=` list)
    while its goal policy, capability cognition and disagreement detector ran as a mage,
    with the carpenter's `decide` rule still choosing what to want — a permanent
    want-vs-refuse standoff the operator's own status line contradicts. That is the
    rule-vs-gate drift class `obsview.py` and `knobs.py` exist to end, re-entered through
    the channel meant to be the safe way to tune. Not a contrived key either:
    `foundry/archive.py::Genome`'s first axis is literally named `profession`, and the
    genome is the searcher this channel is being built for.

    The second is merely loud, but expensively placed. A genuine TYPO
    (`disagreement_tick`) raises `TypeError` from the constructor — and every runner
    builds its Lives AFTER the login, the GM staging, the provenance gold-wipe and the
    seed grant. **So every caller must run this BEFORE the first packet**, not at the
    construction site: a one-character mistake otherwise abandons a spawned, logged-in,
    seeded character behind an unhandled traceback. `LifeSpec` gets that for free by
    checking at spec construction; the inline runners call this at the top of the
    function, above `ResilientIpcBody.spawn`.

    `knob_names` is passed rather than read off a class because `LifeSpec`'s factory is a
    lambda closing over its Life class and cannot be introspected. A caller that passes
    the WRONG set fails in the fail-safe direction — the base `WarriorLife.KNOBS` against
    a `TinkerLife` rejects `bank_trip_surplus` loudly and offline, which is the wrong
    answer but the harmless one.
    """
    knobs = dict(knobs or {})
    unknown = set(knobs) - set(knob_names)
    if unknown:
        raise ValueError(
            f"{label}: {sorted(unknown)} is not a tuning knob. "
            f"Knobs for this Life: {sorted(knob_names)}. Every key must be a "
            "threshold the Life reads back through anima2/knobs.py's clamp; a "
            "constructor parameter that is identity (profession) or cognition "
            "(steering) is not one, and tuning it here recreates the rule-vs-gate "
            "drift this channel exists to avoid.")
    return knobs


def build_tuned_life(life_cls, knobs: dict[str, Any] | None, /, **kwargs):
    """Construct `life_cls` with `knobs` checked against ITS OWN `KNOBS` allowlist.

    `LifeRunner.build_life` is this for the two `LifeSpec` runners, and its docstring
    gives the reason both exist: `run()` needs a live shard, so without a named seam the
    knob channel could only ever be proved by re-implementing the construction line in a
    test — which is exactly how a channel ends up asserted-but-not-wired. The four INLINE
    runners have the same problem and had no such seam, so a test could reach two of the
    seven Life-construction sites. They call this.

    Reading `life_cls.KNOBS` off the class rather than taking the set is the one thing
    this can do that `LifeSpec` cannot: a spec's factory is a lambda and has to be TOLD
    its allowlist (`knob_names`, defaulting to the base set, fail-safe but tellable
    wrong). Here the class is named once and the allowlist follows it.

    It does NOT replace the runner's own early `validate_knobs` call: construction
    happens after the login and the GM staging, and the whole point of checking is to
    fail before the first packet. Same check, run twice, deliberately — the second is
    where the value is USED and so is where a test can watch it arrive.
    """
    return life_cls(**kwargs,
                    **validate_knobs(knobs, life_cls.KNOBS, label=life_cls.__name__))


# --- the harness ---------------------------------------------------------------------

@dataclass
class Staged:
    """What a spec's staging step hands back to the harness."""

    routes: dict
    #: Where this life LIVES — the harness leashes BOTH agent memories to it. The leash
    #: went to one memory of two once, and the profession that runs its economy agent
    #: nearly every tick wandered out of pickup range of its own supply.
    home: tuple[int, int]
    leash: int | None = None
    #: Extra keys for the hunt/work agent's memory (e.g. `harvest_nodes`).
    memory: dict = field(default_factory=dict)
    #: Extra keys for the economy agent's memory (e.g. `craft_spot`).
    econ_memory: dict = field(default_factory=dict)
    #: Gold granted AFTER provenance (a seed the profession genuinely needs to start,
    #: e.g. a carpenter's first board batch — it cannot make its own material). 0 = none.
    seed_gold: int = 0
    banner: str = ""


@dataclass
class LifeSpec:
    """Everything profession-specific about running one Life. No defaults on the
    load-bearing fields: forgetting one is a TypeError at construction, not a lesson
    re-learned against the live shard."""

    profession: str                       #: capability-registry key AND worker job label
    persona_name: str
    account_prefix: str
    life_factory: Callable[..., Any]      #: (body, persona, routes, **knobs) -> Life
    #: (gm, serial, body) -> Staged. Runs inside the GM context; use `stage_shops` and
    #: the module helpers rather than re-implementing them.
    stage: Callable[[GmControl, int, Any], Staged]
    #: Optional extra status fields: (life, obs) -> str appended to the standard line.
    status_extra: Callable[[Any, Observation | None], str] | None = None
    #: Tuning knobs forwarded to `life_factory` as keyword arguments — the harness's
    #: only channel from a caller (a genome, a bandit, a sweep script) into a Life's
    #: thresholds. It has a default because it is genuinely optional, NOT because it is
    #: decoration: audit proposal 5 gave the Lives knob PARAMETERS while the spec's
    #: factory type stayed `(body, persona, routes)`, so a spec could not express a
    #: threshold at all. That gap made CLAUDE.md's deferral precondition (a), "the
    #: genome's axes can steer a full Life", false in practice, and the Phase-7
    #: evolution-vs-random rerun waits on exactly that. Filled by the two shipped
    #: runners' own `knobs=` argument (`village.run_carpenter_life` /
    #: `run_woodsman_life`) — the ENTRY POINT, added because a channel wireless at the
    #: caller's end is a channel only the tests can reach, which is the same "asserted
    #: but not wired" shape `build_life` below exists to prevent. Every key here must be
    #: a knob the Life routes through `anima2/knobs.py` — a raw threshold tuned from out
    #: here is a new drift avenue, not an axis (see that module's docstring for what one
    #: already cost). ENFORCED by `__post_init__` against `knob_names`; that sentence
    #: was a comment and nothing else until a reviewer walked a non-knob through it.
    knobs: dict[str, Any] = field(default_factory=dict)
    #: The allowlist `knobs` is checked against — the KNOBS set of the class this spec's
    #: factory actually builds (`CarpenterLife.KNOBS`, `TinkerLife.KNOBS`, ...). Defaults
    #: to the base `WarriorLife.KNOBS`, which is the right set for four of the five Lives
    #: and FAIL-SAFE for the fifth: a tinker spec that forgets to declare it rejects
    #: `bank_trip_surplus` loudly, offline, at construction — the wrong direction to fail
    #: in, but the harmless one. The factory is a lambda closing over its Life class, so
    #: the spec cannot introspect the set; it has to be told.
    knob_names: frozenset[str] = WarriorLife.KNOBS

    def __post_init__(self) -> None:
        """Reject a `knobs` key that is not a knob, at SPEC construction — the earliest
        point on this path, and the whole reason the check sits here rather than in
        `build_life`: `knobs` is fully known before the first packet.

        The rule and its refusal text are `validate_knobs`, shared with the four inline
        runners so the two halves of the channel can never disagree about what a knob is.
        See it for the two failures this catches and what each one cost.
        """
        self.knobs = validate_knobs(self.knobs, self.knob_names,
                                    label=f"{self.profession} spec")


class LifeRunner:
    """Owns the run sequence every solo Life shares. See the module docstring."""

    def __init__(self, spec: LifeSpec, *, host: str = "127.0.0.1", port: int = 2594,
                 ticks: int = 600, monitor: bool = False,
                 persist_insights: bool = False) -> None:
        self.spec = spec
        self.host, self.port, self.ticks, self.monitor = host, port, ticks, monitor
        self.persist_insights = persist_insights

    def build_life(self, body, routes: dict):
        """Construct the Life the way `run()` does — the spec's factory plus its KNOBS.

        A named seam, not a wrapper for its own sake: `run()` needs a live shard (a
        spawned body, a GM context, a worker thread), so the knob channel could only
        ever be proved by re-implementing this one line in a test, which is exactly how
        a channel ends up asserted-but-not-wired. Offline tests call THIS, and the live
        path calls it too, so "the tuned value reaches the constructed Life" is one
        fact rather than two that must be kept in step.

        The splat is safe only because `LifeSpec.__post_init__` already checked every
        key against the Life's own `KNOBS` allowlist — see it for what rode this line
        unvalidated before, and why the check belongs at spec construction and not here.
        """
        spec = self.spec
        return spec.life_factory(body, Persona(name=spec.persona_name), routes,
                                 **spec.knobs)

    def staged_line(self, life, staged: Staged) -> str:
        """The one line a live operator reads before the day starts: where this Life
        stands, what it was seeded with, and the reserve it will actually keep.

        A named seam for the same reason `build_life` is one. The reserve is read off
        the BUILT Life through `market._bank_reserve` — the single read point the decide
        rule, the `bank_gold` gate and `BankGold`'s own FSM share — and it MUST be read
        here rather than composed by the spec: `run()` calls `spec.stage()` BEFORE
        `build_life()`, so a `Staged.banner` is structurally incapable of seeing a tuned
        value. `run_carpenter_life` baked `carpenter_life.BANK_RESERVE` into its banner
        exactly that way; accurate only while nothing tuned that runner, and it would
        have started printing a false number to a live operator the moment the runner
        grew its `knobs` argument. Call it AFTER `econ_memory` lands — a spec's own
        `econ_memory` may carry `bank_reserve`, and that write is the last one.
        """
        from .skills.movement import leash_readout

        seed = f" and {staged.seed_gold}g seed" if staged.seed_gold else ", broke"
        reserve = _bank_reserve(life.econ_agent.memory)
        # The leash for the same reason as the reserve, and with one extra: it is the one
        # knob something ELSE writes (`set_leash`, from `Staged.leash`), so the number a
        # tuned run actually wanders under is not derivable from either input alone.
        leash = leash_readout(life.econ_agent.memory, life)
        return (f"staged: {self.spec.persona_name}@{staged.home}{seed}"
                f"  (reserve {reserve}, leash {leash})"
                + (f"  {staged.banner}" if staged.banner else ""))

    def run(self, worker: Callable) -> None:
        """`worker` is the village's `_run_worker` (injected to avoid an import cycle —
        village imports this module)."""
        spec = self.spec
        print(f"raising a {spec.profession} at {self.host}:{self.port}")
        acct = f"{spec.account_prefix}{fresh_suffix()}"
        seat = MONITOR_PORT_BASE if self.monitor else None
        try:
            body = ResilientIpcBody.spawn(self.host, self.port, acct, acct,
                                          pump_ms=400, monitor_port=seat)
        except Exception as e:  # noqa: BLE001
            print(f"  {acct}: login failed ({e})")
            return
        watch = f"  watch: http://127.0.0.1:{seat}/" if seat else ""
        print(f"  {acct}: {spec.persona_name} the {spec.profession}{watch}")

        login_throttle(GM_RELOGIN_COOLDOWN_S)
        serial = body.ready["player"]["serial"]
        with GmControl.spawn(self.host, self.port) as gm:
            gm.hide()
            staged = spec.stage(gm, serial, body)
            # Provenance ALWAYS, and always before any seed: measured income must be
            # earned income. This is the harness's guarantee, not the spec's memory.
            enforce_gold_provenance(gm, body, serial)
            if staged.seed_gold:
                gm.command_on(f"[AddToPack Gold {staged.seed_gold}", serial)

        life = self.build_life(body, staged.routes)
        self._wire_persistence(life)
        life.memory.update(staged.memory)
        life.econ_agent.memory.update(staged.econ_memory)
        life.set_leash(staged.home, staged.leash)
        # AFTER `econ_memory` — see `staged_line` for why that ordering is the only
        # correct read point for the reserve.
        print(self.staged_line(life, staged) + "\n")

        status: dict[int, str] = {}
        lock = threading.Lock()
        started = time.monotonic()
        budget = self.ticks * getattr(life, "tick_budget_scale", 1)
        t = threading.Thread(target=worker,
                             args=(life, budget, 0, status, lock, spec.profession),
                             daemon=True)
        t.start()
        while t.is_alive():
            time.sleep(4.0)
            obs = getattr(life.body, "last_obs", None)
            extra = f" {spec.status_extra(life, obs)}" if spec.status_extra else ""
            with lock:
                snap = [status[i] for i in sorted(status)]
            # Net earned per hour: (pack + banked) - seed, over wall time. Provenance
            # deleted the starter gold, so this is genuinely EARNED — and a negative
            # rate is the audit's economic finding made visible live: both deployed
            # craft loops destroy value at vendor prices, and a society scaled on a
            # bleeding loop decays to bankruptcy without a line like this saying so.
            earned = pack_amount(obs, GOLD_GRAPHIC) + banked_amount(obs) - staged.seed_gold
            hours = max(1e-9, (time.monotonic() - started) / 3600.0)
            print(f"— {spec.profession} [{life.mode}] "
                  f"{telemetry_line(life, spec.profession, obs)}{extra} "
                  f"hp={hp_readout(obs)} gold={pack_amount(obs, GOLD_GRAPHIC)} "
                  f"banked={banked_amount(obs)} "
                  f"net={earned:+d}g ({earned / hours:+.0f}g/h) —")
            for line in snap:
                print(f"  {line}")
        t.join(timeout=5)
        print(f"\nthe {spec.profession}'s day is done")


    def _wire_persistence(self, life) -> None:
        """Phase-6 persistence for a Life (audit #8): the hunt agent — the one that
        does the living — reflects through the tiered LLM, its insights persist to
        `data/insights.jsonl` under the persona's key, and a prior session's insights
        are LOADED at construction so live hours finally compound instead of being
        discarded at exit. Mirrors `run_village`'s own live-verified wiring; degrades
        honestly (a note, not a crash) when no LLM provider builds."""
        if not self.persist_insights:
            return
        try:
            from pathlib import Path

            from .cognition import (
                LLMCognition,
                LLMReflection,
                ReflectingCognition,
                ThreadedCognition,
            )
            from .llm import build_tiered_clients, with_role
            from .memory import load_insights

            clients = build_tiered_clients()
            insights = load_insights(Path("data") / "insights.jsonl",
                                     self.spec.persona_name)
            prior = insights.recent(1)
            if prior:
                print(f"  resumed insight: \"{prior[-1].text[:70]}\"")
            # Role-tagged clients (health-check follow-up #2): chatter and reflection
            # both ride the cheap tier under the degraded provider, and untagged they
            # are indistinguishable in the ledger — which is exactly how a
            # heuristic-authored insight passed for an LLM one until forensics
            # compared the TEXT against the fallback template.
            life.hunt_agent.cognition = ThreadedCognition(ReflectingCognition(
                LLMCognition(clients["cheap"], job=self.spec.profession),
                LLMReflection(with_role(clients["cheap"], "reflection")),
                insights=insights,
            ))
            print(f"  persistence: insights load+persist for {self.spec.persona_name}")
        except Exception as e:  # noqa: BLE001 — persistence must never break the life
            print(f"  persistence requested but not wired ({e}); running without")
