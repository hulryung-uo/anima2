"""Hunt/loot — engage weak creatures, kill them, and loot their corpses.

Phase 3 item 3 (DESIGN.md §10): a third composed work skill in the established
style (`MineSmeltDeliver`'s deliver/return pair, `BlacksmithMarket`'s sell/bank
pair) — this time built *on top of* `skills/combat.py::Combat` rather than
duplicating its WarMode/Attack decision logic (`Hunt(Combat)`, mirroring
`MineAndSmelt(Mine)`/`BlacksmithMarket(Blacksmith)`'s own subclass-and-defer
shape).

## Death → corpse → loot, with no new Rust surface

UO death is a **linking** problem, not a new interaction. Killing a mobile
opens a corpse *item* (graphic `0x2006` — `anima-core`'s `net/game.rs` 0xAF
`DisplayDeath` handler) and tells us which corpse belongs to which kill
(`World.corpse_of`, a `corpse_serial -> killed_serial` map) — both already
fully parsed Rust-side (ground truth verified directly against
`anima-core/src/net/game.rs`/`world/mod.rs` and `anima-net/src/json.rs`; see
`contract.py`'s `CorpseLink`/`CorpseEquip` for the mirror this item actually
needed — the only lockstep gap, the same shape as items 1-2's own gaps).
Opening the corpse is an ordinary `Use` (double-click — `Corpse.OnDoubleClick`
→ `Open` → the base container's usual content-packet flow ServUO uses for
*every* container). **Not a gump** — a corpse never shows up in
`Observation.gumps`, so none of `craft.py`'s gump machinery applies here.
Once open, its contents show up in `Observation.items` keyed by
`container == corpse.serial`, exactly like a backpack or bank box; looting one
is the same lift-then-place two-step `MineSmeltDeliver._deliver_step`/
`BlacksmithMarket._bank_step` already established: `PickUp` the item to the
cursor, then `Drop` it into our own backpack. **No Rust changes were needed**
for any of this — `Action.Use`/`PickUp`/`Drop`/`Attack`/`WarMode` and
`Observation.corpse_of`/`corpse_equip` already existed Rust-side.

## Attribution: whose kill is this corpse?

`Combat._target` always attacks the *nearest* hostile, re-evaluated every
tick — there is no sticky "current target" to remember. `Hunt` instead
remembers **every serial it has ever sent an `Attack` for** (`hunt_attacked`,
capped the same defensive way `anima-core`'s own `MAX_CORPSE_LINKS` bounds
`corpse_of`/`corpse_equip`) and, each tick, scans `obs.corpse_of` for a link
whose `killed` is in that set — that corpse is ours to loot. Recorded only on
a tick that actually emits `Attack` (checked against `super().step()`'s own
returned action, at the *end* of `step()`) — not merely a tick where
`Combat._target` computes a nearest-hostile candidate, which also happens on
the WarMode-only tick and every mid-loot tick where `Combat.step()` never
even runs — so a hostile that merely wandered into range while the hunter was
elsewhere never gets misattributed a kill it had nothing to do with. This is
attribution by "we attacked it, and it died", not the server's own looting-
rights model (`Corpse.CheckLoot`/`CanLoot`) — correct for a solo hunter with
nothing else contesting the kill, which is this MVP's whole scenario.

## Loot selection

A conservative whitelist by item graphic (`LOOT_GRAPHICS`): `Gold` (`0x0EED`,
`Scripts/Items/Consumables/Gold.cs`) is the *only* graphic a weak creature's
loot pack can actually produce on this shard in practice — the calibrated
target, Mongbat (`Scripts/Mobiles/Normal/Mongbat.cs`), calls
`AddLoot(LootPack.Poor)`, and `LootPack.Poor` resolves to `OldPoor`
(`Scripts/Misc/LootPack.cs`) because `Core.SE`/`Core.AOS` are both false under
this shard's `T2A` expansion setting (`Config/Expansion.cfg`): **100% chance,
`1d25` gold**, plus a 0.02% chance of a random musical instrument this MVP
doesn't whitelist (too many unverifiable graphics for a chance this small —
Mongbat corpses are gold-only in practice, confirmed by reading the loot-pack
chain directly, not assumed). `Ruby`/`Diamond` (`0x0F13`/`0x0F26`,
`Scripts/Items/Resource/Ruby.cs`/`Diamond.cs`) round out the whitelist as
verified-but-not-exercised valuables for richer loot tables later — a Mongbat
never drops them, so this item's own live proof only ever exercises gold.
`Hunt` **never** reads `Observation.corpse_equip` at all (items the creature
was *wearing*, a different mechanism — worn-layer entries, not container
contents, needing its own unequip-then-lift handling) — out of scope for this
MVP per the ground truth, satisfied simply by not touching that field.

## Reward: observed pack gains only

Combat's own per-`Attack` reward (0.05/tick, `skills/combat.py`) is **not**
this skill's reward signal — `step()` always zeroes out whatever
`Combat.step()` attaches to its own action and pays only for whitelisted
valuables **confirmed gained** in the backpack, using the exact same
"confirmed net gain since a phase-entry baseline, banked across observation
lag" accounting `BlacksmithMarket._sell_step`/`_bank_step` and
`MineSmeltDeliver._deliver_step` already established (`_loot_reward`,
`_bank`/`_payout`): a `None`-returning tick (nothing left to do right now)
would otherwise silently drop a reward earned on the very tick a scan comes
up empty. The baseline itself is never captured against a backpack that
isn't actually *visible* yet (`hunt_val_start` stays unset while
`_backpack(ctx)` is `None`) — otherwise a momentarily-missing backpack on the
very first loot tick would baseline at `0` and falsely pay out whatever
valuables the pack already held the moment it reappeared. And a confirmed
gain can itself lag the corpse queue draining by more than the usual single
tick (an in-flight `Drop` that hasn't landed in the backpack total yet) —
`step()` keeps re-checking the same baseline for `loot_reward_settle_ticks`
ticks *after* the phase has already reset to `engage` before finally clearing
it, so a gain confirmed one or two observations late still gets paid exactly
once instead of silently lost.

## Bounding every stage — no unbounded retry path, anywhere

Every stage that can wait on the server has an explicit, cliloc-independent
bound — the general lesson `craft.py::Blacksmith`'s "third dead gump" bug
teaches (a retry loop with no notion of "this isn't working" hammers a dead
interaction forever), baked in from day one rather than discovered live:

- **`locate`** (walking to the corpse): stall-bounded exactly like
  `BlacksmithMarket._market_walk_toward` (`stall_limit`).
- **`open`**: a fixed settle wait after `Use` (mirrors
  `BlacksmithMarket`'s own `BANK_SETTLE_TICKS`) rather than an unbounded
  "wait for a gump" loop — there is no gump to wait for (see above), so this
  stage structurally cannot hang the way a reshown CraftGump can. If nothing
  at all shows up under the corpse's container after the settle wait (not
  even a non-whitelisted item — a corpse *can* legitimately be empty, so this
  alone isn't proof of failure), it retries the `Use` itself up to
  `open_attempts` times before accepting "nothing here" as the real answer.
  Even then, a corpse that *never* showed any contents at all across every
  retry (`hunt_never_opened`) is treated as a **failed** open, not a
  **successful-but-empty** one: it goes back through the ordinary
  `hunt_giveup` cooldown below, retryable later, rather than being retired
  for good — real loot must never be permanently abandoned just because a
  `Use` happened to bounce every time. A corpse that *did* show contents
  (even a single non-whitelisted item) and simply has nothing whitelisted
  left is, by contrast, genuinely done — see below.
- **`loot`**: each lift-then-place attempt is counted; `loot_attempts` bounds
  how many a single corpse gets (mirrors `BlacksmithMarket.
  BANK_DEPOSIT_ATTEMPTS` — a bounced `Drop` must not retry forever). An
  in-flight lift (`hunt_held`, an item already on the server cursor via
  `PickUp`) is never abandoned mid-air by any give-up path — every route to
  retiring a corpse funnels through `_advance`, which always finishes the
  `Drop` into the backpack first (waiting, bounded, for the backpack to
  reappear if it's momentarily not visible): ServUO rejects all further
  lifts while `Mobile.Holding` is set, so a stranded cursor item would
  neuter every later corpse's looting too, not just this one.
- **`hunt_giveup` cooldown decay**: a corpse that got abandoned (walk wedge,
  a `Use` that never opens it, or a loot lift that keeps bouncing) is not
  retried immediately — the corpse-of scan that rebuilds the loot queue skips
  any corpse still inside `giveup_cooldown_ticks` (30, **the exact constant
  and mechanism `BlacksmithMarket.giveup_cooldown_ticks` uses**) of its last
  give-up. Without this, a permanently-stuck corpse would re-enter the queue
  on the very next tick after being abandoned — a livelock in all but name,
  the same one `MineSmeltDeliver.deliver_giveup_ingots`/
  `BlacksmithMarket.sell_giveup_daggers` guard against elsewhere in this
  package. `hunt_giveup` itself is capped to `max_tracked` entries (evicting
  the oldest give-up ticks first), the same defensive bound `hunt_attacked`/
  `hunt_looted` already get. A corpse that's genuinely fully looted (opened,
  and nothing whitelisted left) is instead marked `hunt_looted`
  **permanently** — never revisited at all, since there's nothing transient
  about "already looted".
"""

from __future__ import annotations

from ..contract import Attack, Drop, PickUp, Position, Use, Walk
from ..geometry import direction_toward
from .base import SkillContext, SkillResult, Status
from .combat import Combat
from .harvest import BACKPACK_LAYER

# ServUO `Scripts/Items/Consumables/Gold.cs`: `base(0xEED)` — every gold pile
# uses this one graphic (mirrors `skills/market.py::GOLD_GRAPHIC`; duplicated
# rather than imported — `market.py` is a blacksmith-specific module this
# skill has no other reason to depend on).
GOLD_GRAPHIC = 0x0EED
# `Scripts/Items/Resource/Ruby.cs` / `Diamond.cs` — verified graphics for a
# richer loot table; not exercised by this item's own live proof (see the
# module docstring: a Mongbat's `LootPack.Poor` only ever drops Gold here).
RUBY_GRAPHIC = 0x0F13
DIAMOND_GRAPHIC = 0x0F26
#: The loot-selection whitelist — gold plus a couple of verified "valuable"
#: item graphics. Deliberately conservative: better to leave real loot behind
#: than to grab something unverified.
LOOT_GRAPHICS = frozenset({GOLD_GRAPHIC, RUBY_GRAPHIC, DIAMOND_GRAPHIC})

# A corpse item's own graphic (`anima-core`'s `net/game.rs` doc comment: "a
# corpse (graphic 0x2006)") — informational only; corpses are *found* via
# `Observation.corpse_of`, not by scanning for this graphic.
CORPSE_GRAPHIC = 0x2006
# `Corpse.Open` (`Scripts/Items/Corpses/Corpse.cs`): `from.IsStaff() ||
# from.InRange(GetWorldLocation(), 2)` — matches the `SELL_REACH`/
# `FORGE_REACH` order of magnitude elsewhere in this package.
CORPSE_REACH = 2


class Hunt(Combat):
    """Engage weak hostiles, then open and loot their corpses.

    See the module docstring for the full design (attribution, loot
    selection, reward accounting, and the bounded-retry discipline every
    stage follows). A strict extension of `Combat`: with no kill ever
    detected (`hunt_queue` always empty), `step()` falls straight through to
    `Combat.step()` every tick but for the reward override (see below) — so
    an already-Combat-tested scenario with no corpse to loot behaves the same
    way `Combat` alone would, action-for-action.
    """

    name = "hunt"
    description = "Engage weak hostile creatures, then open and loot their corpses."

    #: Consecutive no-progress walking ticks before giving up on reaching one
    #: corpse (mirrors `GoTo.stall_limit` / `BlacksmithMarket.stall_limit`).
    stall_limit: int = 6
    #: Ticks to wait for a linked corpse to actually appear in `Observation.
    #: items` before giving up on it (should be near-instant — we killed it
    #: standing right next to it — but never assume that live).
    corpse_find_timeout: int = 10
    #: Fixed settle wait after `Use`-ing a corpse before trusting its
    #: container contents are visible (mirrors `BANK_SETTLE_TICKS`).
    open_settle_ticks: int = 3
    #: How many times to re-`Use` a corpse that shows *no* contents at all
    #: (not even non-whitelisted ones) after the settle wait, before
    #: accepting "nothing here" as the real answer (see the module docstring).
    open_attempts: int = 2
    #: How many lift-then-place attempts one corpse gets in the `loot` stage
    #: before giving up on it (mirrors `BANK_DEPOSIT_ATTEMPTS`).
    loot_attempts: int = 6
    #: How many `step()` ticks a give-up backoff lasts before that corpse is
    #: eligible to be retried (mirrors `BlacksmithMarket.giveup_cooldown_ticks`
    #: — same constant, same reasoning: see the module docstring).
    giveup_cooldown_ticks: int = 30
    #: Extra `step()` ticks, after a loot run drains and the phase resets to
    #: `engage`, that a confirmed pack gain still gets checked (and paid)
    #: against the just-finished run's baseline before it's finally cleared —
    #: closes the window where a gain lands one or two observation ticks
    #: *after* the corpse scan itself came up empty (see the module docstring's
    #: reward section).
    loot_reward_settle_ticks: int = 2
    #: Cap on how many attacked-serial / permanently-looted-corpse entries are
    #: remembered at once (mirrors anima-core's own `MAX_CORPSE_LINKS`).
    max_tracked: int = 64

    def can_run(self, ctx: SkillContext) -> bool:
        if ctx.persona.combat_disposition == "pacifist":
            return False
        return (
            self._target(ctx) is not None
            or bool(ctx.memory.get("hunt_queue"))
            or ctx.memory.get("hunt_phase") == "loot"
        )

    def diagnose(self, ctx: SkillContext) -> str | None:
        """`None` iff `can_run`; else which of `can_run`'s own conditions
        failed (a pacifist persona, vs. genuinely nothing to fight or loot —
        e.g. an empty `hunt_queue` with no hostile in range) — a more useful
        distinction than the ABC default's generic fallback for item 5's
        eligibility reasoning."""
        if ctx.persona.combat_disposition == "pacifist":
            return "pacifist — will not engage"
        if self.can_run(ctx):
            return None
        return "no hostile creatures nearby to engage, and nothing queued to loot"

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs
        tick = ctx.memory["hunt_tick"] = ctx.memory.get("hunt_tick", 0) + 1

        # (Re)build the loot queue from `corpse_of` links: a corpse whose
        # `killed` we attacked, not already queued or permanently looted, and
        # not still inside a give-up cooldown (see the module docstring).
        # Uses `hunt_attacked` as of the *previous* tick's Attack, not this
        # one — a serial this tick's own Attack (recorded below, once we
        # know one was actually sent) targets can't have a corpse observable
        # in this same tick's `obs.corpse_of` anyway (the server hasn't
        # processed the death yet), so the ordering makes no behavioural
        # difference beyond correctly excluding a tick with no Attack at all.
        attacked_set = set(ctx.memory.get("hunt_attacked", ()))
        looted = set(ctx.memory.get("hunt_looted", ()))
        giveup = ctx.memory.get("hunt_giveup", {})
        queue = list(ctx.memory.get("hunt_queue", ()))
        for link in obs.corpse_of:
            if (
                link.killed in attacked_set
                and link.corpse not in looted
                and link.corpse not in queue
                and tick - giveup.get(link.corpse, -(10**9)) >= self.giveup_cooldown_ticks
            ):
                queue.append(link.corpse)
        ctx.memory["hunt_queue"] = queue

        # Only break away from engaging *between* attacks (a kill just
        # landed, or a previous loot run left something queued) — never mid
        # anything Combat itself is doing (Combat has no multi-tick waiting
        # state of its own to preserve, unlike Blacksmith's gump, but this
        # keeps the same "phase switch only at a clean boundary" discipline).
        phase = ctx.memory.get("hunt_phase", "engage")
        if phase == "engage" and queue:
            phase = ctx.memory["hunt_phase"] = "loot"

        if phase == "loot":
            # Reward computed and banked exactly once per tick, *before* any
            # same-tick corpse-to-corpse handoff inside `_loot_step` (see its
            # docstring) — otherwise a recursive call could recompute (and
            # silently re-zero) the very reward this tick already earned.
            self._bank(ctx, self._loot_reward(ctx))
            result = self._loot_step(ctx, tick)
            if result is not None:
                return self._payout(ctx, result)
            ctx.memory["hunt_phase"] = "engage"
            # Don't clear the baseline yet — a confirmed pack gain from this
            # very run can land one or two observation ticks *after* the
            # corpse scan itself came up empty (an in-flight Drop that
            # hadn't reflected in the backpack total yet). Keep checking the
            # same baseline for `loot_reward_settle_ticks` more ticks before
            # finally clearing it (see module docstring).
            ctx.memory["hunt_val_settle"] = self.loot_reward_settle_ticks
        elif ctx.memory.get("hunt_val_settle", 0) > 0:
            self._bank(ctx, self._loot_reward(ctx))
            settle = ctx.memory["hunt_val_settle"] - 1
            if settle <= 0:
                ctx.memory.pop("hunt_val_settle", None)
                ctx.memory.pop("hunt_val_start", None)
                ctx.memory.pop("hunt_val_paid", None)
            else:
                ctx.memory["hunt_val_settle"] = settle

        combat_result = super().step(ctx)
        # Remember every serial we've actually sent an `Attack` for — not
        # merely "was the computed target" (`Combat._target` re-targets
        # "nearest hostile" fresh every tick, including WarMode-only ticks
        # and ticks where `super().step()` never even runs, e.g. mid-loot
        # above) — only a tick that actually emits `Attack` counts (see
        # module docstring).
        if isinstance(combat_result.action, Attack):
            attacked = list(ctx.memory.get("hunt_attacked", ()))
            if combat_result.action.serial not in attacked:
                attacked.append(combat_result.action.serial)
                ctx.memory["hunt_attacked"] = attacked[-self.max_tracked :]
        # This skill's own reward signal is loot, not combat activity (see
        # the module docstring) — Combat's per-attack reward never leaves here.
        return self._payout(ctx, SkillResult(combat_result.status, combat_result.action, 0.0))

    # --- loot phase --------------------------------------------------------------

    def _loot_reward(self, ctx: SkillContext) -> float:
        """Valuables confirmed gained in the pack since this loot run began,
        banked exactly like `BlacksmithMarket._sell_step`'s gold accounting:
        `confirmed_gain = max(0, now - start)`, paying only the increment not
        already covered by `hunt_val_paid` (never double-pays the same gain on
        a later tick where the pack total merely hasn't changed again).
        """
        if self._backpack(ctx) is None:
            # The backpack must actually be *visible* before any baseline is
            # trustworthy — capturing `hunt_val_start = 0` while it's merely
            # not-yet-observed (rather than confirmed empty) would falsely
            # attribute whatever pre-existing valuables it already held as a
            # fresh gain the moment it reappears (see module docstring).
            return 0.0
        val_now = self._pack_valuables(ctx)
        start = ctx.memory.get("hunt_val_start")
        if start is None:
            start = ctx.memory["hunt_val_start"] = val_now
        paid = ctx.memory.get("hunt_val_paid", 0.0)
        confirmed_gain = max(0, val_now - start)
        reward = confirmed_gain - paid
        if reward > 0:
            ctx.memory["hunt_val_paid"] = paid + reward
            return reward
        return 0.0

    def _loot_step(self, ctx: SkillContext, tick: int) -> SkillResult | None:
        """One loot-phase tick for `hunt_queue[0]`, or `None` once the whole
        queue is drained (the caller resumes `engage`).

        State machine (`ctx.memory["hunt_loot_stage"]`): `locate` (walk to the
        corpse) → `open` (`Use`, then a fixed settle wait) → `loot`
        (lift-then-place each whitelisted item in turn). Reward is handled
        entirely by the caller (`step()`) — every `SkillResult` this method
        returns carries `reward=0.0`; see `step()`'s own docstring note on why.
        """
        queue = ctx.memory.get("hunt_queue", [])
        if not queue:
            return None
        corpse_serial = queue[0]
        corpse = self._item_by_serial(ctx, corpse_serial)
        stage = ctx.memory.get("hunt_loot_stage", "locate")

        def _advance(*, permanent: bool) -> SkillResult | None:
            """Retire `corpse_serial` (fully looted, or abandoned this round)
            and move on to whatever's next in the queue — same tick, matching
            `BlacksmithMarket._walk_route`'s "same tick, next leg" recursion
            (nothing changes about *this* tick's reward by doing so; see
            `step()`'s docstring).

            Never abandons with an item still lifted onto the server cursor
            (`hunt_held`): ServUO rejects all further lifts while `Mobile.
            Holding` is set, so a lift stranded mid-air here would neuter
            every later corpse's looting too, not just this one. If
            something's still held, finish its `Drop` into the backpack
            first — waiting (bounded by `corpse_find_timeout`) for the
            backpack to reappear if it's momentarily not visible — before any
            of the retirement bookkeeping below runs, no matter which stage
            or condition triggered this abandon.
            """
            held = ctx.memory.get("hunt_held")
            if held is not None:
                bp = self._backpack(ctx)
                if bp is not None:
                    ctx.memory.pop("hunt_held", None)
                    ctx.memory.pop("hunt_held_wait", None)
                    return SkillResult(Status.RUNNING, Drop(serial=held, container=bp.serial), 0.0)
                wait = ctx.memory.get("hunt_held_wait", 0) + 1
                ctx.memory["hunt_held_wait"] = wait
                if wait < self.corpse_find_timeout:
                    return SkillResult(Status.RUNNING, None, 0.0)
                # Backpack never reappeared — nothing more we can do about
                # it; stop tracking (the item bounces server-side on its own,
                # see module docstring) rather than wait forever, matching
                # every other stage's bounded-retry discipline.
                ctx.memory.pop("hunt_held", None)
                ctx.memory.pop("hunt_held_wait", None)

            queue.pop(0)
            ctx.memory["hunt_queue"] = queue
            if permanent:
                done = list(ctx.memory.get("hunt_looted", ()))
                done.append(corpse_serial)
                ctx.memory["hunt_looted"] = done[-self.max_tracked :]
            else:
                gave_up = dict(ctx.memory.get("hunt_giveup", {}))
                gave_up[corpse_serial] = tick
                if len(gave_up) > self.max_tracked:
                    # Same defensive cap as `hunt_attacked`/`hunt_looted`
                    # (mirrors anima-core's own `MAX_CORPSE_LINKS`) — evict
                    # the *oldest* give-ups first (by the tick they were
                    # abandoned), not insertion order, so a corpse that keeps
                    # bouncing back into cooldown doesn't get evicted just
                    # because its entry was first created long ago.
                    gave_up = dict(sorted(gave_up.items(), key=lambda kv: kv[1])[-self.max_tracked :])
                ctx.memory["hunt_giveup"] = gave_up
            for key in ("hunt_loot_stage", "hunt_loot_stall", "hunt_loot_last_pos",
                        "hunt_open_settle", "hunt_open_attempts", "hunt_find_wait",
                        "hunt_loot_attempts", "hunt_held", "hunt_held_wait", "hunt_never_opened"):
                ctx.memory.pop(key, None)
            return self._loot_step(ctx, tick)

        if corpse is None:
            wait = ctx.memory.get("hunt_find_wait", 0) + 1
            ctx.memory["hunt_find_wait"] = wait
            if wait >= self.corpse_find_timeout:
                return _advance(permanent=False)  # never showed up — try again later
            return SkillResult(Status.RUNNING, None, 0.0)
        ctx.memory.pop("hunt_find_wait", None)

        if stage == "locate":
            if corpse.distance > CORPSE_REACH:
                step = self._walk_toward_corpse(ctx, corpse.pos.x, corpse.pos.y)
                if step is not None:
                    return step
                return _advance(permanent=False)  # wedged — try again later
            ctx.memory.pop("hunt_loot_stall", None)
            ctx.memory.pop("hunt_loot_last_pos", None)
            stage = ctx.memory["hunt_loot_stage"] = "open"

        if stage == "open":
            settle = ctx.memory.get("hunt_open_settle")
            if settle is None:
                attempts = ctx.memory.get("hunt_open_attempts", 0) + 1
                ctx.memory["hunt_open_attempts"] = attempts
                ctx.memory["hunt_open_settle"] = 0
                return SkillResult(Status.RUNNING, Use(serial=corpse_serial), 0.0)
            settle += 1
            if settle < self.open_settle_ticks:
                ctx.memory["hunt_open_settle"] = settle
                return SkillResult(Status.RUNNING, None, 0.0)
            # Settle elapsed. A corpse *can* legitimately be empty (see module
            # docstring), so "nothing whitelisted" alone doesn't mean the
            # `Use` failed — but "nothing attributed to it at all" is at least
            # ambiguous, worth one honest retry before accepting it.
            has_contents = self._corpse_has_contents(ctx, corpse_serial)
            if not has_contents and ctx.memory["hunt_open_attempts"] < self.open_attempts:
                ctx.memory.pop("hunt_open_settle", None)
                return SkillResult(Status.RUNNING, None, 0.0)
            ctx.memory.pop("hunt_open_settle", None)
            ctx.memory.pop("hunt_open_attempts", None)
            # Retries exhausted with *no* contents ever attributed to this
            # corpse — a `Use` that genuinely never opened it, indistinguishable
            # client-side from a successful-but-empty open. Remember that here
            # so the "loot" stage below retires it via the give-up cooldown
            # (retryable later) rather than permanently, unlike a corpse that
            # *did* show contents (even non-whitelisted ones) and simply has
            # nothing worth taking (see module docstring).
            ctx.memory["hunt_never_opened"] = not has_contents
            stage = ctx.memory["hunt_loot_stage"] = "loot"

        # stage == "loot"
        held = ctx.memory.get("hunt_held")
        if held is not None:
            bp = self._backpack(ctx)
            if bp is None:
                # Don't discard `hunt_held` here — it's still lifted on the
                # server cursor. `_advance`'s own held-guard (see its
                # docstring) will keep retrying the Drop instead of abandoning
                # with the item stranded mid-air.
                return _advance(permanent=False)
            ctx.memory.pop("hunt_held", None)
            return SkillResult(Status.RUNNING, Drop(serial=held, container=bp.serial), 0.0)

        item = self._corpse_loot_item(ctx, corpse_serial)
        if item is None:
            never_opened = ctx.memory.pop("hunt_never_opened", False)
            # A corpse whose `Use` never actually opened it (see the "open"
            # stage above) gets a giveup-cooldown retry, not permanent
            # retirement — real loot must never be abandoned forever just
            # because the open attempts happened to bounce.
            return _advance(permanent=not never_opened)  # nothing (more) whitelisted — done for good

        attempts = ctx.memory.get("hunt_loot_attempts", 0)
        if attempts >= self.loot_attempts:
            return _advance(permanent=False)  # every lift-then-place so far bounced
        ctx.memory["hunt_loot_attempts"] = attempts + 1
        ctx.memory["hunt_held"] = item.serial
        return SkillResult(Status.RUNNING, PickUp(serial=item.serial, amount=item.amount), 0.0)

    def _walk_toward_corpse(self, ctx: SkillContext, tx: int, ty: int) -> SkillResult | None:
        """One greedy step toward `(tx, ty)`, `stall_limit`-bounded like
        `BlacksmithMarket._market_walk_toward`. `None` means wedged.
        """
        here = ctx.obs.player.pos
        cur = (here.x, here.y)
        stall = ctx.memory.get("hunt_loot_stall", 0) + 1 if ctx.memory.get("hunt_loot_last_pos") == cur else 0
        ctx.memory["hunt_loot_stall"] = stall
        ctx.memory["hunt_loot_last_pos"] = cur
        if stall >= self.stall_limit:
            ctx.memory.pop("hunt_loot_stall", None)
            ctx.memory.pop("hunt_loot_last_pos", None)
            return None
        d = direction_toward(here, Position(x=tx, y=ty, z=here.z))
        return SkillResult(Status.RUNNING, Walk(dir=d, run=False), 0.0)

    # --- reward carry (mirrors MineAndSmelt._bank / BlacksmithMarket._stash_reward) --

    def _bank(self, ctx: SkillContext, reward: float) -> None:
        if reward:
            ctx.memory["hunt_banked_reward"] = ctx.memory.get("hunt_banked_reward", 0.0) + reward

    def _payout(self, ctx: SkillContext, result: SkillResult) -> SkillResult:
        banked = ctx.memory.pop("hunt_banked_reward", 0.0)
        if not banked:
            return result
        return SkillResult(result.status, result.action, result.reward + banked)

    # --- lookups -------------------------------------------------------------------

    @staticmethod
    def _item_by_serial(ctx: SkillContext, serial: int):
        return next((i for i in ctx.obs.items if i.serial == serial), None)

    @staticmethod
    def _corpse_has_contents(ctx: SkillContext, corpse_serial: int) -> bool:
        return any(i.container == corpse_serial for i in ctx.obs.items)

    @staticmethod
    def _corpse_loot_item(ctx: SkillContext, corpse_serial: int):
        return next(
            (i for i in ctx.obs.items if i.container == corpse_serial and i.graphic in LOOT_GRAPHICS), None,
        )

    def _pack_valuables(self, ctx: SkillContext) -> int:
        bp = self._backpack(ctx)
        if bp is None:
            return 0
        return sum(i.amount for i in ctx.obs.items if i.graphic in LOOT_GRAPHICS and i.container == bp.serial)

    @staticmethod
    def _backpack(ctx: SkillContext):
        # Mirrors `Harvest._backpack`/`Blacksmith._backpack` (filter by owner,
        # not just layer — a nearby mobile's own backpack, or a corpse's
        # `BACKPACK_LAYER`-free container, could otherwise be ambiguous).
        return next(
            (i for i in ctx.obs.items if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial), None,
        )
