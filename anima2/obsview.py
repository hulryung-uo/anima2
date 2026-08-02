"""obsview — the ONE definition of what an Observation says we have.

Every Life asks the same four questions of every tick: what is in OUR backpack, what is
worn on US, what is lying loose within reach, and how much is in OUR bank box. Those
questions had **twenty** answers across the five Life modules (`warrior_life.py`,
`mage_life.py`, `woodsman_life.py`, `carpenter_life.py`, `tinker_life.py`) and four more
in `life_runner.py` — copy-pasted forward each time a profession was added, which is the
same "carrying lessons forward is the part I keep skipping" defect `life_runner.py`'s own
docstring was written to name.

The drift was never theoretical. Three of them are on the record:

- **The distance clause.** A carpenter wandered nine tiles off its drop and spent a whole
  run wanting `fetch_boards` for boards it could merely SEE — a goal admission must
  refuse (`adm=fetch_boards` zero times in 1200 ticks). The fix was one `distance <=
  PICKUP_RADIUS` clause, and `mage_life._ground_gold`'s own docstring recorded that "the
  carpenter's identical fix was never back-ported" — the audit found exactly that drift
  latent in the mage (docs/AUDIT-2026-07-29.md).
- **The held-or-worn widening.** A worn axe read as "no tool" and cost a lumberjack an
  entire run: the skill said "I have an axe", the rule said "so process the logs", and the
  gate said "no tool", so nothing was ever admitted. `capabilities._owned_tool` was
  widened to accept a WORN tool; the Lives were widened one at a time, by hand.
- **The missing-backpack guard.** `warrior_life._has_weapon` guarded `bp is not None`; the
  three Lives written after it did not, so `i.container in (bp, player)` matched every
  GROUND item the moment our own pack was out of view — a rule claiming to own a saw that
  the gate (correctly) says we do not. See `owns` below, which fixes it once. The fix has
  TWO halves and only both together close the disagreement: the ground-side readbacks
  (`on_ground`/`ground_amount`) had to gain the SAME clause, because every fetch gate opens
  with `_backpack_serial(ctx) is not None` and a fetch branch guarded only by `owns` would
  simply want a different capability the gate still refuses.

A single definition makes back-porting UNNECESSARY rather than remembered. That is the
whole point: the fix above is not "remember to update five files", it is "there is one
file". These are pure functions over an `Observation` — no `SkillContext`, no memory, no
body — so they sit below every Life and above nothing, and they stay testable without one.

`life_runner.py` re-exports `pack_serial`, `pack_amount`, `banked_amount` and
`owned_tool_readout` from here, because `village.py`, the live bank gates and
`tests/test_life_runner.py` import them from there; moving the definitions must not move
anybody's imports.

**Deliberately NOT merged into this module** (each would be a regression, not a cleanup):
`capabilities.py`'s gate-side readbacks and `skills/*.py`'s closures take a
`SkillContext`, not an `Observation`, and they sit BELOW this module in the layering —
`obsview` imports `BACKPACK_LAYER`/`GOLD_GRAPHIC`/`BANKBOX_LAYER`/`PICKUP_RADIUS` *from*
`skills`, so a `skills` module importing `obsview` would be an import cycle
(`skills/woodwork.py`'s deferred `# noqa: E402` import is standing evidence that package
is already under cycle pressure). Unifying the gate side is the natural sequel, and `owns`
is written to mirror `capabilities._owned_tool` clause-for-clause so that day is cheap.
"""

from __future__ import annotations

from collections.abc import Iterable

from .contract import Observation
from .skills.craft import PICKUP_RADIUS
from .skills.harvest import BACKPACK_LAYER
from .skills.hunt import GOLD_GRAPHIC
from .skills.market import BANKBOX_LAYER

#: An item art, or any collection of them. Every readback below accepts either, because
#: the five Lives split evenly on which they passed: `warrior`/`mage`/`woodsman` wrote
#: `i.graphic == graphic` and `carpenter`/`tinker` wrote `i.graphic in graphics`. The two
#: shapes were one wrong call apart — handing a frozenset to the `==` variant returned a
#: silent **0**, not an error. One signature, both callers, no trap.
Graphics = int | Iterable[int]


def _arts(graphics: Graphics) -> Iterable[int]:
    """A bare art becomes a one-element set; a collection of arts is used as-is."""
    return {graphics} if isinstance(graphics, int) else graphics


def pack_serial(obs: Observation | None) -> int | None:
    """OUR backpack — owner-filtered, since a neighbour's pack shares the layer.

    `None` when the pack is not in the observation at all, and `None` for a `None` obs:
    callers read `body.last_obs`, which is genuinely empty before the first pump.
    """
    if obs is None:
        return None
    return next((i.serial for i in obs.items
                 if i.layer == BACKPACK_LAYER and i.container == obs.player.serial), None)


def bank_serial(obs: Observation | None) -> int | None:
    """OUR bank box — owner-filtered for the same reason `pack_serial` is: standing at a
    bank, every nearby character's box is in the observation at the same layer."""
    if obs is None:
        return None
    return next((i.serial for i in obs.items
                 if i.layer == BANKBOX_LAYER and i.container == obs.player.serial), None)


def pack_amount(obs: Observation | None, graphics: Graphics) -> int:
    """Summed amount of `graphics` (an art or a set of them) in OUR backpack.

    Two clauses are deliberate merges of variants that had drifted apart:

    - **`bp is None`, not a falsy test.** `warrior`/`mage`/`woodsman` wrote
      `... if bp else 0`, which reads a backpack whose serial is literally `0` as ABSENT.
      Unreachable on a real shard — ServUO item serials start at `0x40000000` and
      `ItemView.from_dict` reads `serial` with no default — but it is one hand-written
      fixture away from a silent zero, so the falsy form does not come back.
    - **`in`, not `==`.** See the `Graphics` note above; the `==` variants answered 0 for
      a set instead of raising.
    """
    if obs is None:
        return 0
    bp = pack_serial(obs)
    if bp is None:
        return 0
    arts = _arts(graphics)
    return sum(i.amount for i in obs.items if i.graphic in arts and i.container == bp)


def pack_has(obs: Observation | None, graphics: Graphics) -> bool:
    """Does OUR backpack hold ANY item of `graphics` — existence, not quantity.

    Deliberately NOT `pack_amount(...) > 0`. `ItemView.amount` defaults to 0 (`contract.py`
    reads it as `d.get("amount", 0)`), so a single non-stacking item whose wire payload
    omits `amount` — a sword, a saw, an axe — sums to zero while plainly being there.
    Collapsing the two would make "is there a sword in the pack" answer no while holding
    one; the warrior's upgrade branch reads exactly that.
    """
    if obs is None:
        return False
    bp = pack_serial(obs)
    if bp is None:
        return False
    arts = _arts(graphics)
    return any(i.graphic in arts and i.container == bp for i in obs.items)


def bank_amount(obs: Observation | None, graphics: Graphics) -> int:
    """Summed amount of `graphics` in OUR bank box — `pack_amount`'s bank-box twin.

    Parameterised by art rather than pinned to gold because a second reader wanted it
    that way: `curriculum.py::_bankbox_amount` is graphics-parameterised (its
    `_bankbox_threshold` milestone builder takes a graphics set), and narrowing it to
    gold to fit `banked_amount` would have changed that builder's documented shape. So
    the general form lives here and `banked_amount` below is its gold binding — the
    lesson stays where it applies instead of being generalized into vagueness.
    """
    if obs is None:
        return 0
    box = bank_serial(obs)
    if box is None:
        return 0
    arts = _arts(graphics)
    return sum(i.amount for i in obs.items if i.graphic in arts and i.container == box)


def banked_amount(obs: Observation | None) -> int:
    """Gold sitting in THIS character's bank box — the deposit proof `live_bank_goal.py`
    uses. A falling pack count alone is not evidence of banking: it is equally
    consistent with spending it, dropping it, or dying with it."""
    return bank_amount(obs, GOLD_GRAPHIC)


def owns(obs: Observation | None, graphics: Graphics, *, layer: int | None = None) -> bool:
    """An item of `graphics` WORN on us or in OUR backpack. Ground never counts.

    This is the definition the skills, the profession rules and the readiness gate all
    share — it mirrors `capabilities._owned_tool` clause-for-clause on purpose, so the
    rule can never want a capability the gate refuses (the disagreement class the
    concordance suite exists to kill).

    **Worn counts, and that is the whole point.** The gate used to accept only a PACKED
    tool, which quietly disagreed with everything around it: the skills that swing the
    tool (`ProcessLogs._axe`, `Harvest._tool`) accept a worn one, the rules that decide
    what to do next accept a worn one, and so does ServUO — `BaseAxe.OnDoubleClick` asks
    for reach and accessibility, not for a backpack. Live-caught with a lumberjack, which
    of course WIELDS its axe: every part was individually right and the agent did nothing
    for an entire run. The full story is in `capabilities._owned_tool`'s docstring.

    **The pack arm is not redundant either.** A blade or a chest plate JUST BOUGHT is in
    the pack and not yet worn; a rule that looked only at the worn slot would send the
    warrior back to the vendor for a second one.

    `layer` pins the worn slot when the profession needs it: a warrior's blade only counts
    at `WEAPON_LAYER` and its chest plate only at its own body layer, because ANY worn
    plate at any layer is not "wearing a chest". A tool just has to be on us, so tools
    pass `layer=None`.

    **`bp is not None` is a fix, not decoration.** `carpenter._owns`, `tinker._owns_tool`
    and `woodsman._has_axe` all wrote `i.container in (bp, player)` (or `== bp`) with no
    guard, and a ground item's container IS `None` — so the moment our own pack was out of
    the observation, a saw lying on the ground read as OWNED while the gate
    (`capabilities._owned_tool`, guarded since it was written) refused.
    `warrior._has_weapon` had carried the guard from the start and it was never
    forward-ported. Every fixture in the suite injects a backpack, which is why nothing
    caught it offline.

    What that actually cost, stated no larger than it is (measured, not assumed): the rule
    SKIPS its `fetch_saw`/`buy_saw` branch and falls through to a lower one — for the
    carpenter `fetch_boards` when boards are on the ground, else `hunt`; for the woodsman
    `hunt`. It cannot reach `craft_carpentry`, because with no pack in the observation
    `pack_amount` answers 0 for the boards that branch requires (the tinker's `craft_tongs`
    is the same shape). And this guard alone does NOT close the disagreement in that state
    — see `on_ground` below, which needed the mirror-image clause for the same reason.
    """
    if obs is None:
        return False
    arts = _arts(graphics)
    bp = pack_serial(obs)
    owner = obs.player.serial
    return any(
        i.graphic in arts
        and ((i.container == owner and (layer is None or i.layer == layer))
             or (bp is not None and i.container == bp))
        for i in obs.items
    )


def ground_amount(obs: Observation | None, graphics: Graphics) -> int:
    """Summed amount of `graphics` lying loose in the world WITHIN PICKUP REACH and
    FETCHABLE BY US — never our own pack.

    Both clauses mirror the fetch gate, because the gate has both:

    - **Distance.** Without `distance <= PICKUP_RADIUS` this rule wanted `fetch_gold` for
      a purse it could merely SEE, which admission must refuse — the audit found exactly
      that drift latent here after the carpenter's identical fix was never back-ported
      (docs/AUDIT-2026-07-29.md), and the concordance suite now fails on it if either side
      moves alone.
    - **Our own backpack is in the observation.** See `on_ground` below, which carries the
      whole story: a pickup needs somewhere to put the item, every fetch gate says so in
      its first line, and a ground readback that did not say it made the rule want a fetch
      the gate always refuses.

    The radius is deliberately NOT a parameter. Its only correct value is the one the gate
    uses; making it caller-tunable would reopen precisely the drift this module closes.
    """
    if obs is None or pack_serial(obs) is None:
        return 0
    arts = _arts(graphics)
    return sum(i.amount for i in obs.items
               if i.graphic in arts and i.container is None
               and i.distance <= PICKUP_RADIUS)


def on_ground(obs: Observation | None, graphics: Graphics) -> bool:
    """Is ANY item of `graphics` lying loose WITHIN PICKUP REACH and FETCHABLE BY US —
    existence, not quantity.

    The question is "is there something here for us to FETCH", not "is something visible",
    because every caller is a fetch branch. So it carries the fetch gate's OWN two
    preconditions, and it is the only place they are written on the rule side:

    **Distance.** The fetch gate requires a ground item within `PICKUP_RADIUS`, and a rule
    that ignored distance would want `fetch_boards` for boards it can merely SEE across the
    clearing — a goal admission must then refuse, producing the stall this project keeps
    paying for. Live-caught with a carpenter, which wandered nine tiles off its drop and
    spent the whole run wanting a pickup the gate would never allow (`adm=fetch_boards`
    zero times in 1200 ticks). The woodsman's axe fetch and the tinker's delivered-iron
    fetch ride the same clause.

    **Our own backpack is in the observation.** A pickup needs somewhere to put the item,
    and all five fetch gates open with exactly that line (`_make_tool_fetch_ready`,
    `_make_fetch_ready`, `_make_fetch_ready_set`, `_fetch_ready` — every one of them starts
    `_backpack_serial(ctx) is not None`). Review-caught with a probe in the same session
    `owns` gained its `bp is not None` guard, and it is the reason that guard is not enough
    on its own: fixing the OWNED side alone merely renamed the disagreement. With a hatchet
    at our feet and our pack out of view, the woodsman rule went from wanting nothing to
    wanting `fetch_hatchet` against `ready=[]` — 2,009 newly-disagreeing states in a
    30,000-state random sweep, plus 611 for the carpenter's `fetch_saw` (and 14 more where
    it merely renamed a `fetch_boards` refusal) — while the mage's `fetch_gold` had been
    wanting the same refusal in 291 of them since it was written. A
    disagreement that persists for `disagreement_ticks` is reported every tick AND closes a
    UI surface every tick (`warrior_life._clear_stale_ui`), on a perfectly healthy agent.
    `docs/WOODSMAN.md` records the pack-out-of-view state as real, not hypothetical.

    Deliberately NOT `ground_amount(...) > 0`, for the same reason `pack_has` is not
    `pack_amount(...) > 0`: a dropped axe or saw whose wire payload omits `amount` sums to
    zero while lying right there.
    """
    if obs is None or pack_serial(obs) is None:
        return False
    arts = _arts(graphics)
    return any(i.graphic in arts and i.container is None and i.distance <= PICKUP_RADIUS
               for i in obs.items)


def owned_tool_readout(obs: Observation | None, graphics: Graphics) -> str:
    """`"yes"`/`"NO"` for a tool WE could actually use — ours or loose on the ground,
    never a neighbour's. Reporting "yes" for the Weaponsmith's axe is how a status line
    once hid the exact defect that broke a run.

    Its container set is deliberately DIFFERENT from `owns`: this one includes `None`
    (ground). A status line answers "is a usable tool within reach", not "do we have
    one" — a tool lying at our feet is a run that will recover, and a status line that
    said NO for it would be lying in the other direction. Merging the two would be a
    regression whichever way it went, so they stay two functions with two meanings.

    For the same reason it does NOT carry `on_ground`'s backpack clause: that clause
    exists to mirror the fetch GATE, and a status line mirrors no gate. Reporting "NO"
    for a hatchet at our feet because our pack blinked out of the observation would hide
    the recoverable case, which is the failure mode this readout was written against.
    """
    if obs is None:
        return "NO"
    arts = _arts(graphics)
    bp = pack_serial(obs)
    return "yes" if any(i.graphic in arts
                        and i.container in (bp, obs.player.serial, None)
                        for i in obs.items) else "NO"
