"""Live gate: the exit-edge hold's THIRD bound — an overdue frame releases the hold.

`WarriorLife.tick`'s hold is bounded three ways. The 2026-08-03 live runs exercised
bound 2 (the frame's own deadline) twice and nothing else: `docs/AUDIT-2026-07-29.md`
§6 records `FRAME OVERDUE` printed ZERO times in 306 status samples across three runs,
so `frame_overdue` releasing the hold — and `_repair_overdue_frame` pointing the
stale-UI repair at the frame — have never run on a shard. That is the least-verified
part of the fix and it is precisely the part that stops the fix being WORSE than the
defect: the first version of the hold, without this bound, was measured pinning four of
five Lives in economy mode with `hunt_after = 0` for a whole 3000-tick window (audit §5)
— a total livelock where the pre-hold code merely carried a zombie frame and kept
hunting.

WHY AN ORDINARY RUN NEVER GETS THERE. Two comparators race for the same frame:

    GoalStack.expire_due   fires at  econ ticks >= deadline_tick   (agent.py)
    WarriorLife.tick       fires at  econ ticks >  deadline_tick   (frame_overdue)

so whenever the frame CAN yield on its deadline tick, expire_due wins by one tick and
bound 3 never runs. Bound 3 is reachable only through a frame that cannot yield at the
exact deadline tick — which is why the 2026-08-03 forge run's three wedged frames all
left on bound 2 at 176-177/180 and 292/300, and why the same run's 14 `ui=gump` samples
on `craft_tongs` (ages 3-23) were the right surface at the wrong time.

WHAT THIS GATE FORCES, and why each piece is the piece it is:

  * a `craft_tongs` frame, not the cheaper `buy_iron` one. `_craft_can_yield` refuses
    on `started and not terminal` AND on any open surface, so mistiming the wound by a
    tick still leaves the frame unyieldable; a craft opens its surface IN PLACE with one
    `Use(tool)` — no vendor NPC, no walk, no popup handshake (all documented live
    failure modes; forge16's stale window WAS a failed buy trip).
  * the craft FSM's OWN gump, left open. Nothing is injected: the surface this gate
    repairs is the one the capability itself opened, which is the only version of the
    wedge a live shard can produce.
  * a STARVE, because the surface will not stay open by itself. `CraftItemCapability.
    max_goal_steps = 240` is BELOW the 300-tick craft deadline, so an ordinary craft
    aborts, closes its own gump and leaves on bound 2 — measured offline. `Survive` is
    `skills[0]` of the capability planner for every profession (`profession.py`), so a
    wounded character's economy agent is ticked every tick while its capability FSM is
    never stepped: nothing answers the gump, and `expire_due` still runs (it sits
    upstream of skill selection in `Agent.tick`). The wound is `[Set RawStr 2000` +
    `[Set Hits 50` — HitsMax becomes `Str/2 + 50` = 1050 (`PlayerMobile.cs`), so 50 hits
    is 4.8% against `Survive.heal_below_fraction` of 40%, a margin neither natural regen
    nor a 0-Healing bandage can close inside the window.
  * a TELEPORT off the craft spot, which is what engages the hold at all: the rule then
    answers `hunt` while the frame is live, so `mode == "economy"` is the ORCHESTRATOR's
    doing and dropping it is observable. Without it the tinker's own rule keeps wanting
    `craft_tongs`, the economy agent ticks anyway, and the release half of bound 3 is
    invisible.

ONE RUN GETS BOTH HALVES, in consecutive economy ticks: at `deadline_tick + 1` the
repair closes the gump and returns True, so the hold is EXTENDED one tick; at
`deadline_tick + 2` there is no surface left, the repair returns False, and the hold
DROPS. The frame is deliberately still on the stack at the end — `_craft_can_yield`
still refuses on `started and not terminal` once the gump is gone — which is the
documented worst case, "a stale frame, but alive", not a failure.

Verdict flags (all must hold):
  staged_craft_gump  — a `craft_tongs` frame was live with the FSM's own gump open when
                       the wound landed (the surface is earned, not asserted)
  fsm_starved        — `cap_craft_steps` never moved again after the wound; if it climbed,
                       the starve broke and a passing run would prove nothing
  hold_engaged       — the orchestrator held economy mode for a frame its own rule had
                       stopped wanting (`want=None`, `holding_frame`), BEFORE the deadline
  went_overdue       — `frame_overdue` became True, at exactly `deadline_tick + 1` economy
                       ticks (the comparator, not merely "late")
  repair_closed_surface — `_repair_overdue_frame` closed the gump ON that first overdue
                       tick, and the next observation came back with no surface at all
  hold_released      — the hold dropped with the frame still on the stack and still
                       reported overdue (`!frozen!overdue`), and it dropped LATER than
                       the first overdue tick by exactly the number of closes the repair
                       spent. That arithmetic is the point: with nothing to close the
                       hold drops on the overdue tick ITSELF, so a non-zero extension is
                       the numeric signature of the repair — and `<= OVERDUE_REPAIRS`
                       is the proof the extension stayed capped rather than becoming the
                       unbounded hold in a new costume
  not_the_detector   — `rule_gate_disagreement` stayed None throughout, so the close is
                       attributable to `_repair_overdue_frame` and NOT to
                       `_detect_disagreement`'s copy of the same repair (which needs an
                       EMPTY goal stack, so the two can never collide — this flag is the
                       evidence, not the argument)

What this gate does NOT prove: bound 1 (the FSM give-up ladder) is not exercised here,
the death override is not exercised here, and the `OVERDUE_REPAIRS` cap is not reached
(one close is spent, of three). No economy claim is made — no gold moves, and the run
ends with a wounded tinker standing beside a stale frame on purpose.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from .control import GmControl
from .ipc_body import ResilientIpcBody
from .life_runner import enforce_gold_provenance
from .live_common import (
    GM_RELOGIN_COOLDOWN_S,
    fresh_suffix,
    login_throttle,
    print_gate_verdict,
    wipe_area,
)
from .persona import Persona
from .profession import PROFESSIONS, TRADE_SMITH_SPOT
from .skills.tinkering import TinkerTongs
from .tinker_life import TinkerLife

HOST, PORT = "127.0.0.1", 2594
#: Comfortably more than one batch, so `craft_tongs` is admitted on the first tick
#: (`craft_material_per_item * craft_batch`, from the capability's own config).
IRON = TinkerTongs.craft_material_per_item * TinkerTongs.craft_batch * 4
#: `Survive` needs a bandage to bandage with: `can_run` returns False for a
#: bandage-less agent with no hostiles, and then the FSM is not starved at all.
BANDAGES = 50
#: Raise the ceiling rather than lower the floor. `PlayerMobile.HitsMax` is
#: `Str / 2 + 50`, so RawStr 2000 buys a 1050-point bar and the 50 hits below sit at
#: 4.8% of it — far under `Survive.heal_below_fraction` (0.40) with room for every
#: bandage a 0-Healing tinker can apply in the window.
WOUND_RAWSTR = 2000
WOUND_HITS = 50
#: Re-wound this often, unconditionally. Cheap insurance (~8 GM commands over the
#: window): if regeneration ever did lift the character over the trigger, `Survive`
#: would hand the hands back, the FSM would answer its own gump, and the run would
#: silently degrade into an ordinary bound-2 close.
REWOUND_EVERY = 40
#: Far enough outside `TinkerTongs.craft_spot_radius` (3) that the rule answers `hunt`
#: even if the character drifts a tile.
TELEPORT_AWAY = 8


@dataclass
class FrameOverdueWatch:
    """Tick-by-tick witness for the three moments this gate exists to catch.

    Deliberately NOT read off a status line: `life_runner` samples every 4.0 s (~9
    ticks) and the `+hold!overdue` pairing exists for exactly ONE tick, so sampling
    misses it ~8 times in 9 — which is how the 2026-08-03 runs missed `+hold` entirely
    and reported four things about themselves that were wrong. This records every tick.

    Shared by the live `main()` below and by the offline tests that drive the same path
    over `MockBody`, so the judgement the gate publishes is the judgement the offline
    reproduction already killed a mutant with.
    """

    life: object
    #: Set by `arm()`: the frame under test, and the state the starve must freeze.
    frame_id: int | None = None
    deadline_tick: int | None = None
    craft_steps_at_arm: int | None = None
    gump_at_arm: bool = False
    ticks: int = 0
    #: `(orchestrator tick, economy tick)` for each moment, or None if it never came.
    overdue_at: tuple[int, int] | None = None
    repair_at: tuple[int, int] | None = None
    released_at: tuple[int, int] | None = None
    hold_ticks_before_overdue: int = 0
    disagreements: int = 0
    closes: int = 0
    closes_while_overdue: int = 0
    craft_steps_seen: set[int] = field(default_factory=set)
    #: Checked on the tick AFTER the close, never on the close's own tick: the action
    #: is emitted from inside `life.tick()`, after that tick's observation was taken, so
    #: `last_obs` still carries the surface when the close lands. Reading it there would
    #: report "STILL OPEN" on a perfectly good repair.
    surface_gone_after_repair: bool | None = None
    frame_live_at_release: bool = False
    overdue_at_release: bool = False

    def _econ(self) -> int:
        return self.life.econ_agent.ticks

    def arm(self, obs) -> None:
        """Latch the frame under test at the moment the wound is about to land."""
        frame = self.life.econ_agent.goal_stack.current
        self.frame_id = None if frame is None else frame.id
        self.deadline_tick = None if frame is None else frame.deadline_tick
        self.craft_steps_at_arm = self.life.econ_agent.memory.get("cap_craft_steps")
        self.gump_at_arm = bool(getattr(obs, "gumps", None))
        self.closes = getattr(self.life, "_stale_ui_closes", 0)

    def record(self) -> str | None:
        """Call once after every `life.tick()`. Returns a line worth printing, or None.

        Every moment is captured on the tick it happens, and the returned line is the
        gate's own unthrottled announcement — the instrumentation the last live run did
        not have.
        """
        life = self.life
        self.ticks += 1
        econ = self._econ()
        now = (self.ticks, econ)
        frame = life.econ_agent.goal_stack.current
        obs = getattr(life.body, "last_obs", None)
        steps = life.econ_agent.memory.get("cap_craft_steps")
        if type(steps) is int:
            self.craft_steps_seen.add(steps)
        if life.rule_gate_disagreement is not None:
            self.disagreements += 1
        closes = getattr(life, "_stale_ui_closes", 0)
        closed_now = closes > self.closes
        self.closes = closes
        line = None
        # The deferred surface readback (see the field's own note) — this tick's
        # observation is the first one taken after last tick's close was emitted.
        if self.repair_at is not None and self.surface_gone_after_repair is None:
            self.surface_gone_after_repair = not (
                getattr(obs, "gumps", None) or getattr(obs, "shop_buy", None)
                or getattr(obs, "shop_sell", None))
            line = (f"** the closed surface is "
                    f"{'GONE' if self.surface_gone_after_repair else 'STILL OPEN'} in "
                    f"the next observation (orch {self.ticks} / econ {econ}) **")
        if life.holding_frame and self.overdue_at is None:
            self.hold_ticks_before_overdue += 1
        if self.overdue_at is None and life.frame_overdue:
            self.overdue_at = now
            line = (f"** FRAME OVERDUE at orch {self.ticks} / econ {econ} "
                    f"(deadline_tick={self.deadline_tick}) — the hold's third bound **")
        if closed_now and life.frame_overdue:
            self.closes_while_overdue += 1
            if self.repair_at is None:
                self.repair_at = now
            line = (f"** REPAIR #{self.closes_while_overdue} at orch {self.ticks} / "
                    f"econ {econ} — _repair_overdue_frame closed a surface and the hold "
                    f"is extended one economy tick **")
        if (self.released_at is None and self.overdue_at is not None
                and life.mode == "hunt" and not life.holding_frame):
            self.released_at = now
            self.frame_live_at_release = frame is not None
            self.overdue_at_release = bool(life.frame_overdue)
            line = (f"** HOLD RELEASED at orch {self.ticks} / econ {econ} — mode=hunt, "
                    f"frame {'still live' if self.frame_live_at_release else 'retired'}, "
                    f"overdue={self.overdue_at_release}, extension="
                    f"{econ - self.overdue_at[1]} econ tick(s) **")
        return line

    def status(self) -> str:
        life = self.life
        frame = life.econ_agent.goal_stack.current
        age = None if frame is None else life.econ_agent.ticks - frame.created_tick
        obs = getattr(life.body, "last_obs", None)
        return (f"econ={life.econ_agent.ticks} mode={life.mode} want={life.target_cap} "
                f"age={age}/{self.deadline_tick} hold={life.holding_frame} "
                f"overdue={life.frame_overdue} "
                f"steps={life.econ_agent.memory.get('cap_craft_steps')} "
                f"gumps={len(getattr(obs, 'gumps', None) or ())} "
                f"hp={None if obs is None else obs.player.hits}")

    def extension(self) -> int | None:
        """Economy ticks the repair bought the hold past its first overdue tick.

        The whole numeric argument of this gate. With NOTHING to close,
        `_repair_overdue_frame` returns False on the first overdue tick and the hold
        drops on that same tick — extension 0. Each successful close returns True, so
        the hold survives one more economy tick — and it is capped at `OVERDUE_REPAIRS`
        per frame, which is what keeps the extension from being an unbounded hold in a
        new costume. So `extension == closes_while_overdue`, and both are visible.
        """
        if self.overdue_at is None or self.released_at is None:
            return None
        return self.released_at[1] - self.overdue_at[1]

    def flags(self) -> dict[str, bool]:
        """The verdict — every clause a thing that was MEASURED, not inferred."""
        from .warrior_life import OVERDUE_REPAIRS

        overdue_econ = None if self.overdue_at is None else self.overdue_at[1]
        extension = self.extension()
        return {
            "staged_craft_gump": self.gump_at_arm and self.frame_id is not None,
            # An empty `craft_steps_seen` would pass a naive `<= 1` check while proving
            # nothing, so require the arm value to be the ONLY value ever seen.
            "fsm_starved": (self.craft_steps_at_arm is not None
                            and self.craft_steps_seen == {self.craft_steps_at_arm}),
            "hold_engaged": self.hold_ticks_before_overdue > 0,
            "went_overdue": (overdue_econ is not None and self.deadline_tick is not None
                             and overdue_econ == self.deadline_tick + 1),
            "repair_closed_surface": (self.repair_at is not None
                                      and self.repair_at == self.overdue_at
                                      and self.surface_gone_after_repair is True),
            # Not merely "the mode went to hunt": the frame must still be on the stack
            # and still reported overdue (the `!frozen!overdue` end state), and the
            # release must be LATER than the first overdue tick by exactly the number of
            # closes the repair spent — which is how a run tells a repaired release from
            # the cheap surface-free one, and proves the extension stayed capped.
            "hold_released": (self.released_at is not None
                              and self.frame_live_at_release
                              and self.overdue_at_release
                              and extension is not None
                              and extension == self.closes_while_overdue
                              and 1 <= extension <= OVERDUE_REPAIRS),
            "not_the_detector": self.disagreements == 0,
        }


def _drive_to_open_gump(life, limit: int = 40) -> tuple[bool, int]:
    """Tick until a `craft_tongs` frame is live AND the FSM's own gump is up.

    Deterministic rather than a race: nothing server-side answers the gump between two
    `life.tick()` calls, so the window this opens is as wide as the caller needs.
    """
    for tick in range(1, limit + 1):
        life.tick()
        frame = life.econ_agent.goal_stack.current
        obs = getattr(life.body, "last_obs", None)
        cap = None if frame is None else frame.goal.params.get("capability")
        if cap == "craft_tongs" and getattr(obs, "gumps", None):
            return True, tick
        time.sleep(0.1)
    return False, limit


def main() -> int:
    acct = f"animaoverdue{fresh_suffix()}"
    with ResilientIpcBody.spawn(HOST, PORT, acct, acct, pump_ms=400) as body:
        serial = body.ready["player"]["serial"]
        login_throttle(GM_RELOGIN_COOLDOWN_S)
        gm = GmControl.spawn(HOST, PORT).__enter__()
        try:
            gm.hide()
            tx, ty, tz = gm.stage(serial, *TRADE_SMITH_SPOT,
                                  skills=PROFESSIONS["tinker"].skills,
                                  items=["TinkerTools 999", f"IronIngot {IRON}",
                                         f"Bandage {BANDAGES}"])
            # A clean floor: a stray ingot in pickup reach would make `fetch_iron` the
            # honest winner and the craft frame would never be admitted.
            wipe_area(gm, tx, ty, radius=8, z=tz)
            # No seed gold, and the starter purse deleted: with NO banker and NO vendor
            # staged, every other branch of the tinker's rule is unreachable, so the
            # only thing that can move the mode is the craft branch — which is what the
            # teleport below takes away.
            enforce_gold_provenance(gm, body, serial)

            life = TinkerLife(body=body, persona=Persona(name="Pim"), routes={})
            for m in (life.memory, life.econ_agent.memory):
                m["craft_spot"] = (tx, ty)
            life.set_leash((tx, ty), 2)
            print(f"staged: Pim@({tx},{ty}) iron={IRON} bandages={BANDAGES} "
                  f"no vendor, no banker — craft_tongs is the only branch\n")

            armed, at = _drive_to_open_gump(life)
            obs = getattr(life.body, "last_obs", None)
            frame = life.econ_agent.goal_stack.current
            cap = None if frame is None else frame.goal.params.get("capability")
            print(f"  t{at:03} admitted={cap} gumps="
                  f"{[g.gump_id for g in (getattr(obs, 'gumps', None) or ())]} "
                  f"econ={life.econ_agent.ticks} "
                  f"steps={life.econ_agent.memory.get('cap_craft_steps')}")
            if not armed:
                print("  the craft frame never opened its own gump — nothing to prove")
                return 1

            watch = FrameOverdueWatch(life=life)
            watch.arm(obs)
            budget = watch.deadline_tick - frame.created_tick

            # THE STARVE, then the exit edge. Order matters: wound first, so the craft
            # FSM is already off the hands when the teleport lands and cannot react to
            # it (`skills/craft.py` aborts and CLOSES its gump on the proximity cliloc —
            # breaking a craft mid-flight accelerates the close, it does not wedge it).
            print(f"  wounding: [Set RawStr {WOUND_RAWSTR} -> HitsMax ~"
                  f"{WOUND_RAWSTR // 2 + 50}, [Set Hits {WOUND_HITS}")
            gm.command_on(f"[Set RawStr {WOUND_RAWSTR}", serial)
            gm.command_on(f"[Set Hits {WOUND_HITS}", serial)
            away = (tx + TELEPORT_AWAY, ty)
            print(f"  teleporting off the craft spot: {away} "
                  f"(radius {TinkerTongs.craft_spot_radius}) — the rule now says hunt")
            gm.command_on(f"[Set X {away[0]} Y {away[1]} Z {tz}", serial)
            # Follow the character so the periodic re-wound stays in GM range, and move
            # the wander leash with it: if `Survive` ever let go, `Wander` would walk
            # the tinker back onto its craft spot and the rule would want the economy
            # again on its OWN account, silently un-engaging the hold.
            gm.go(*away)
            life.set_leash(away, 2)

            print(f"  riding the frame to its deadline: budget={budget} econ ticks, "
                  f"deadline_tick={watch.deadline_tick}\n")
            for tick in range(1, budget + 12):
                life.tick()
                line = watch.record()
                if line is not None:
                    print(f"  t{tick:03} {line}")
                    print(f"        {watch.status()}")
                elif tick % 25 == 0:
                    print(f"  t{tick:03} {watch.status()}")
                if watch.released_at is not None:
                    break
                if tick % REWOUND_EVERY == 0:
                    gm.command_on(f"[Set Hits {WOUND_HITS}", serial)
                time.sleep(0.1)
            print(f"\n  final: {watch.status()}")
            print(f"  moments: overdue={watch.overdue_at} repair={watch.repair_at} "
                  f"released={watch.released_at} extension={watch.extension()} "
                  f"closes_while_overdue={watch.closes_while_overdue} "
                  f"hold_ticks_before_overdue={watch.hold_ticks_before_overdue} "
                  f"craft_steps_seen={sorted(watch.craft_steps_seen)}")
            return 0 if print_gate_verdict(watch.flags(),
                                           label="FRAME OVERDUE GATE") else 1
        finally:
            try:
                gm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 — a teardown must not eat the verdict
                pass


if __name__ == "__main__":
    sys.exit(main())
