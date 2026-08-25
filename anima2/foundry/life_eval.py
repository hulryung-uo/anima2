"""Measure a LIFE offline — the half of the tuning channel that had no searcher.

`foundry/eval.py` stages through `GmControl` and needs a live `IpcBody`, so every
evaluation this project could run cost shard time; and it measures a bare `Agent` with one
work skill, never a Life, so it has never seen an orchestrator, an economy leg, or a coin
reach a bank box. `docs/AUTONOMY-ROADMAP.md` §E's criterion — "every searched axis changes
a meaningful live trajectory" — was therefore blocked on both ends at once: nothing
searched the channel, and nothing could have afforded to.

`MockBanker` (audit §64, follow-up 22's remaining half) removed the second half of that.
A Life's full economy leg now runs offline: walk out, right-click the banker, take the
Bank entry, drop the surplus, walk home, and retire the frame ACHIEVED. So a knob can be
scored against banked gold thousands of times for free, and only the winner needs a shard.

**This is a SEARCH harness, not a proof.** An offline day is a flat world with no latency;
it can tell you that `bank_reserve = 400` banks differently from `bank_reserve = 129`, and
it cannot tell you which is better on Felucca. The intended shape is: search here, confirm
live, and let the audit's prediction discipline judge the confirmation.

**And it can only search one of the five knobs — measured, not assumed.** The first sweep
scored `bank_return_reach`, `econ_grace`, `wander_leash` and `disagreement_ticks` at
IDENTICAL banked gold across every value, with and without prey staged. All four bite only
when something goes wrong, and the thing that goes wrong in every live case is the warrior
being HURT: `Survive` seizes the economy agent, the character moves while it fights and no
longer ends a trip on `bs_stand` (audit §63), and the mode switches often enough for
`econ_grace`'s hysteresis to matter. **`MockBody` has no damage model** — creatures do not
strike, so the player is never wounded and none of that state is reachable.

Injecting the wound directly was tried and does not rescue it: a schedule harsh enough to
move the warrior drops it below `heal_below_fraction`, where §56's rule refuses the
economy leg outright and the day banks nothing at all (`landed=0/0`); a gentler one leaves
the axes flat. There is no band between.

So the honest scope is: this searches axes whose effect is not damage-mediated —
`bank_reserve` today — and the interesting ones still need a shard. That is not a defect
in the harness, it is why `docs/AUTONOMY-ROADMAP.md` §E's criterion says "live
trajectory". Giving `MockBody` combat would turn a packet double into a game simulator,
which its own docstring refuses ("a body double owes the FSM the packets it sends and
nothing else").

It builds through `life_runner.build_tuned_life` on purpose — the same seam the six
production runners use — so a searched value travels the channel a shard would, clamps
where a shard would clamp, and is refused by the same allowlist. A parallel construction
path would make every result an artefact of the harness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..contract import ItemView, MobileView, PlayerView, Position
from ..goals import GoalOutcome
from ..mock_body import MockBanker, MockBody
from ..persona import Persona
from ..skills.harvest import BACKPACK_LAYER
from ..skills.hunt import GOLD_GRAPHIC

#: Where the offline day is staged. Real UO ground, for the reason `MockBody`'s own
#: `bounds` docstring gives: a convenient small box silently refuses every walk at a
#: production coordinate, and the fixture then measures nothing.
STAND = (2587, 408)
BANK = (2587, 413)

_PLAYER, _PACK, _GOLD, _BANKER, _BOX = 0x1, 0x50, 0x900, 0xB0, 0xB1


@dataclass(frozen=True)
class LifeTrial:
    """One offline day for one Life configuration."""

    life_cls: type
    #: Passed to `build_tuned_life`, so an unknown name is REFUSED here exactly as it is
    #: on a shard — a searcher that invents an axis finds out at construction.
    knobs: Mapping[str, Any] = field(default_factory=dict)
    ticks: int = 400
    #: Loot the warrior starts holding. The bank trip is what the trial measures, so the
    #: purse has to start above whatever reserve is being searched.
    gold: int = 2000
    routes: Mapping[str, Any] | None = None
    leash: int = 3
    #: Pinned hostiles, as offsets from the stand. WITHOUT THESE THE DAY IS INERT: the
    #: first sweep this harness ran scored `bank_return_reach`, `econ_grace`,
    #: `wander_leash` and `disagreement_ticks` at **identical** banked gold across every
    #: value, because all four only bite when something goes wrong and nothing goes wrong
    #: in an empty flat world with one uninterrupted trip. A fitness landscape flat on
    #: four of five axes is not searchable.
    #:
    #: The pressures those knobs answer to are all downstream of being ATTACKED: `Survive`
    #: interrupts the economy agent, the warrior moves while it fights so it no longer
    #: ends the trip on `bs_stand` (§63), and the mode switches often enough for
    #: `econ_grace`'s hysteresis to matter.
    prey: tuple[tuple[int, int], ...] = ()
    prey_hits: int = 100


@dataclass(frozen=True)
class LifeResult:
    """What an offline day produced. `banked` is the fitness signal."""

    banked: int
    pack_gold: int
    frames: int
    achieved: int
    ticks: int

    @property
    def landed(self) -> str:
        return f"{self.achieved}/{self.frames}"


def _stage(trial: LifeTrial) -> tuple[MockBody, Any]:
    from ..life_runner import build_tuned_life

    body = MockBody(player=PlayerView(serial=_PLAYER, name="Eval",
                                      pos=Position(*STAND, 0), hits=150, hits_max=150,
                                      body=0x190))
    body.items[_PACK] = ItemView(serial=_PACK, graphic=0x0E75, amount=1, pos=Position(),
                                 container=_PLAYER, layer=BACKPACK_LAYER, distance=0)
    body.items[_GOLD] = ItemView(serial=_GOLD, graphic=GOLD_GRAPHIC, amount=trial.gold,
                                 pos=Position(), container=_PACK, layer=0, distance=0)
    body.mobiles[_BANKER] = MobileView(serial=_BANKER, name="Banker",
                                       pos=Position(*BANK, 0), body=0x190, notoriety=1,
                                       hits=50, hits_max=50, distance=0)
    body.bankers[_BANKER] = MockBanker(serial=_BANKER, box_serial=_BOX)

    for n, (dx, dy) in enumerate(trial.prey):
        serial = 0xC0 + n
        body.mobiles[serial] = MobileView(
            serial=serial, name="Ettin", pos=Position(STAND[0] + dx, STAND[1] + dy, 0),
            body=1, notoriety=6, hits=trial.prey_hits, hits_max=trial.prey_hits,
            distance=0)

    routes = dict(trial.routes) if trial.routes else {"banker_spot": (BANK,)}
    life = build_tuned_life(trial.life_cls, dict(trial.knobs),
                            body=body, persona=Persona(name="Eval"), routes=routes)
    life.set_leash(STAND, trial.leash)
    return body, life


def run_life_trial(trial: LifeTrial) -> LifeResult:
    """Tick one Life through an offline day and report what reached the bank box."""
    body, life = _stage(trial)
    for _ in range(trial.ticks):
        life.tick()

    history = life.econ_agent.goal_stack.history
    banked = sum(i.amount for i in body.items.values()
                 if i.graphic == GOLD_GRAPHIC and i.container == _BOX)
    pack = sum(i.amount for i in body.items.values()
               if i.graphic == GOLD_GRAPHIC and i.container == _PACK)
    return LifeResult(banked=banked, pack_gold=pack, frames=len(history),
                      achieved=sum(1 for f in history if f.outcome is GoalOutcome.SUCCESS),
                      ticks=trial.ticks)


def sweep(life_cls: type, axis: str, values, **trial_kw) -> dict[Any, LifeResult]:
    """Score one axis across `values` — the smallest thing a searcher needs.

    Returns `{value: LifeResult}` in the order given, so a caller can see both the
    fitness and whether the day was healthy enough for it to mean anything (a `banked`
    of 0 with `frames` of 0 is a broken fixture, not a bad knob).
    """
    return {v: run_life_trial(LifeTrial(life_cls=life_cls, knobs={axis: v}, **trial_kw))
            for v in values}
