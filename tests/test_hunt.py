"""The Hunt skill's decision logic — hand-built observations, no live server."""

from anima2.contract import (
    Attack,
    CorpseEquip,
    CorpseEquipEntry,
    CorpseLink,
    Drop,
    ItemView,
    MobileView,
    Observation,
    PickUp,
    PlayerView,
    Position,
    Use,
    Walk,
    WarMode,
)
from anima2.persona import Persona
from anima2.skills.base import SkillContext, Status
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import (
    CORPSE_GRAPHIC,
    CORPSE_REACH,
    DIAMOND_GRAPHIC,
    GOLD_GRAPHIC,
    LOOT_GRAPHICS,
    RUBY_GRAPHIC,
    Hunt,
)

PLAYER = 1
BACKPACK = 0x50
MOB = 0xAA
MOB2 = 0xAB
CORPSE = 0xBB
CORPSE2 = 0xBC
GOLD_SERIAL = 0x900


def _backpack():
    return ItemView(serial=BACKPACK, graphic=0x0E75, amount=1, pos=Position(),
                    container=PLAYER, layer=BACKPACK_LAYER, distance=0)


def _pack_gold(serial, amount):
    return ItemView(serial=serial, graphic=GOLD_GRAPHIC, amount=amount, pos=Position(),
                    container=BACKPACK, layer=0, distance=0)


def _mongbat(serial=MOB, distance=1, hits=5):
    return MobileView(serial=serial, name="a mongbat", pos=Position(101, 100, 0), body=39,
                      notoriety=3, hits=hits, hits_max=hits, distance=distance)


def _corpse(serial=CORPSE, distance=1, pos=Position(101, 100, 0)):
    return ItemView(serial=serial, graphic=CORPSE_GRAPHIC, amount=1, pos=pos,
                    container=None, layer=0, distance=distance)


def _corpse_gold(corpse_serial, serial, amount):
    return ItemView(serial=serial, graphic=GOLD_GRAPHIC, amount=amount, pos=Position(),
                    container=corpse_serial, layer=0, distance=0)


def _corpse_item(corpse_serial, serial, graphic, amount=1):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=corpse_serial, layer=0, distance=0)


def _obs(items, *, mobiles=(), corpse_of=(), corpse_equip=(), pos=Position(100, 100, 0)):
    return Observation(player=PlayerView(serial=PLAYER, pos=pos), items=list(items),
                       mobiles=list(mobiles), corpse_of=list(corpse_of), corpse_equip=list(corpse_equip))


def _ctx(obs, *, memory=None, persona=None):
    return SkillContext(obs=obs, persona=persona or Persona(name="Ash", combat_disposition="aggressive"),
                        memory=memory if memory is not None else {})


# --- graphics verified against ../servuo -----------------------------------------


def test_loot_graphics_match_servuo():
    # Scripts/Items/Consumables/Gold.cs: base(0xEED). Scripts/Items/Resource/Ruby.cs
    # / Diamond.cs: base(0xF13) / base(0xF26). anima-core net/game.rs doc comment:
    # "a corpse (graphic 0x2006)".
    assert GOLD_GRAPHIC == 0x0EED
    assert RUBY_GRAPHIC == 0x0F13
    assert DIAMOND_GRAPHIC == 0x0F26
    assert LOOT_GRAPHICS == {GOLD_GRAPHIC, RUBY_GRAPHIC, DIAMOND_GRAPHIC}
    assert CORPSE_GRAPHIC == 0x2006


# --- engage: reuses Combat, but pays no per-attack reward -------------------------


def test_pacifist_never_hunts():
    ctx = _ctx(_obs([_backpack()], mobiles=[_mongbat()]),
              persona=Persona(name="Pax", combat_disposition="pacifist"))
    assert not Hunt().can_run(ctx)


def test_no_target_no_queue_cannot_run():
    ctx = _ctx(_obs([_backpack()]))
    assert not Hunt().can_run(ctx)


def test_engage_wars_then_attacks_with_zero_reward():
    ctx = _ctx(_obs([_backpack()], mobiles=[_mongbat()]))
    skill = Hunt()
    assert skill.can_run(ctx)
    first = skill.step(ctx)
    assert isinstance(first.action, WarMode) and first.action.on is True
    assert first.reward == 0.0
    second = skill.step(ctx)
    assert isinstance(second.action, Attack) and second.action.serial == MOB
    # Combat's own per-attack reward (0.05, skills/combat.py) must never leak
    # through Hunt — this skill only pays for confirmed loot.
    assert second.reward == 0.0
    assert ctx.memory["hunt_attacked"] == [MOB]


def test_hunt_attacked_not_recorded_on_warmode_only_tick():
    # The WarMode-toggle tick computes a target but never sends an Attack —
    # attribution must not record a serial until an Attack is actually sent.
    ctx = _ctx(_obs([_backpack()], mobiles=[_mongbat()]))
    first = Hunt().step(ctx)
    assert isinstance(first.action, WarMode)
    assert "hunt_attacked" not in ctx.memory


def test_hunt_attacked_not_recorded_on_mid_loot_tick():
    # Already mid-loot (a corpse from an earlier kill still queued) when an
    # unrelated hostile wanders into range: `Combat.step` never runs during
    # the loot phase, so no Attack packet is ever sent for it — it must not
    # be recorded as "attacked" just because it was the nearest hostile.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 5)
    wanderer = _mongbat(serial=0xCD, distance=3)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents], mobiles=[wanderer],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    skill.step(ctx)
    assert 0xCD not in ctx.memory["hunt_attacked"]


def test_ignores_innocents_like_combat_does():
    blue = MobileView(serial=0xCC, name="townsfolk", pos=Position(101, 100, 0), body=0x190,
                      notoriety=1, hits=50, hits_max=50, distance=1)
    ctx = _ctx(_obs([_backpack()], mobiles=[blue]))
    assert not Hunt().can_run(ctx)


# --- kill detection / attribution -------------------------------------------------


def test_kill_of_attacked_mob_switches_to_loot_phase():
    mem = {"hunt_attacked": [MOB]}
    ctx = _ctx(_obs([_backpack(), _corpse()], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    Hunt().step(ctx)
    assert mem["hunt_phase"] == "loot"
    assert mem["hunt_queue"] == [CORPSE]


def test_corpse_of_unattributed_kill_is_ignored():
    # A corpse from a mobile we never attacked (e.g. someone/something else's
    # kill) must never enter our loot queue.
    mem = {"hunt_attacked": [MOB]}
    ctx = _ctx(_obs([_backpack(), _corpse(serial=CORPSE2)],
                    corpse_of=[CorpseLink(corpse=CORPSE2, killed=0xFFFF)]), memory=mem)
    Hunt().step(ctx)
    assert mem.get("hunt_queue", []) == []
    assert mem.get("hunt_phase", "engage") == "engage"


def test_can_run_true_mid_loot_even_with_no_live_target():
    ctx = _ctx(_obs([_backpack()]), memory={"hunt_phase": "loot"})
    assert Hunt().can_run(ctx)


# --- locate: walk to the corpse, stall-bounded give-up ----------------------------


def test_walks_toward_corpse_out_of_reach():
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE]}
    far = _corpse(distance=CORPSE_REACH + 5, pos=Position(110, 100, 0))
    ctx = _ctx(_obs([_backpack(), far], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    result = Hunt().step(ctx)
    assert isinstance(result.action, Walk)
    # "locate" is the implicit default stage — no transition has happened yet.
    assert mem.get("hunt_loot_stage", "locate") == "locate"


def test_wedged_walk_gives_up_with_cooldown_not_permanently():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_tick": 0}
    far = _corpse(distance=CORPSE_REACH + 5, pos=Position(110, 100, 0))
    ctx = _ctx(_obs([_backpack(), far], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    for _ in range(skill.stall_limit + 1):
        result = skill.step(ctx)
    assert result.status is Status.SUCCESS  # nothing left to do this tick
    assert mem["hunt_queue"] == []
    assert CORPSE in mem["hunt_giveup"]
    assert CORPSE not in mem.get("hunt_looted", [])  # transient, not permanent

    # Immediately re-scanning corpse_of must NOT re-queue it (cooldown active).
    skill.step(ctx)
    assert mem["hunt_queue"] == []

    # Once the cooldown has fully elapsed, it's eligible again.
    mem["hunt_tick"] = mem["hunt_giveup"][CORPSE] + skill.giveup_cooldown_ticks
    skill.step(ctx)
    assert mem["hunt_queue"] == [CORPSE]


# --- open: Use once, settle, retry-if-empty ---------------------------------------


def test_open_sends_use_once_then_waits_settle():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE]}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 7)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    first = skill.step(ctx)
    assert isinstance(first.action, Use) and first.action.serial == CORPSE
    assert mem["hunt_loot_stage"] == "open"
    for _ in range(skill.open_settle_ticks - 1):
        waiting = skill.step(ctx)
        assert waiting.action is None
        assert waiting.status is Status.RUNNING
    # Settle elapsed, contents visible → straight into loot (a PickUp, same tick).
    after = skill.step(ctx)
    assert isinstance(after.action, PickUp) and after.action.serial == GOLD_SERIAL
    assert mem["hunt_loot_stage"] == "loot"


def test_open_retries_use_when_nothing_shows_up_at_all():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE]}
    # An empty corpse: no items at all attributed to its container.
    ctx = _ctx(_obs([_backpack(), _corpse()], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    uses = []
    for _ in range(3 + skill.open_settle_ticks * skill.open_attempts):
        r = skill.step(ctx)
        if isinstance(r.action, Use):
            uses.append(r.action)
        if mem.get("hunt_queue", [CORPSE]) == [] or mem.get("hunt_loot_stage") == "loot":
            break
    # It retried the Use at least once (open_attempts >= 2) before giving up on
    # "nothing here" — never assumed a single failed Use meant the container
    # was genuinely empty.
    assert len(uses) == skill.open_attempts
    # A corpse that NEVER showed any contents at all (not even a
    # non-whitelisted item) is indistinguishable from a `Use` that simply
    # never opened it — retired via the give-up cooldown (retryable later),
    # never permanently, so real loot behind a bounced `Use` isn't abandoned
    # for good.
    assert CORPSE in mem.get("hunt_giveup", {})
    assert CORPSE not in mem.get("hunt_looted", [])
    assert mem.get("hunt_queue") == []
    assert mem.get("hunt_phase") == "engage"


def test_open_with_contents_then_nothing_whitelisted_is_permanent():
    # Unlike the never-showed-any-contents case above: a corpse that DID open
    # (a non-whitelisted item was attributed to it) and simply has nothing
    # worth taking is genuinely done — permanent, not a retryable give-up.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE]}
    junk = _corpse_item(CORPSE, 0x901, graphic=0x0FB4)  # not in LOOT_GRAPHICS
    ctx = _ctx(_obs([_backpack(), _corpse(), junk],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    for _ in range(3 + skill.open_settle_ticks):
        skill.step(ctx)
        if mem.get("hunt_queue", [CORPSE]) == []:
            break
    assert mem.get("hunt_looted") == [CORPSE]
    assert CORPSE not in mem.get("hunt_giveup", {})


# --- loot: whitelist selection, lift-then-place, corpse_equip never touched ------


def test_loot_lift_then_place_two_step():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 9)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    lift = skill.step(ctx)
    assert isinstance(lift.action, PickUp) and lift.action.serial == GOLD_SERIAL and lift.action.amount == 9
    place = skill.step(ctx)
    assert isinstance(place.action, Drop) and place.action.serial == GOLD_SERIAL and place.action.container == BACKPACK


def test_loot_skips_non_whitelisted_items():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    junk = _corpse_item(CORPSE, 0x901, graphic=0x0FB4)  # not in LOOT_GRAPHICS
    ctx = _ctx(_obs([_backpack(), _corpse(), junk],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    result = skill.step(ctx)
    # Nothing whitelisted present → done with this corpse for good, without
    # ever trying to pick up the junk item.
    assert mem["hunt_looted"] == [CORPSE]
    assert mem["hunt_queue"] == []
    assert result.status is Status.SUCCESS  # falls through to Combat with nothing to fight


def test_never_reads_corpse_equip():
    # A corpse whose ONLY loot is via `corpse_equip` (a weapon it was
    # wearing) and nothing in its container contents — Hunt must never try
    # to pick that up (out of scope for this MVP; see hunt.py's docstring).
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    ctx = _ctx(
        _obs([_backpack(), _corpse()],
            corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)],
            corpse_equip=[CorpseEquip(corpse=CORPSE, entries=[CorpseEquipEntry(layer=1, serial=0x9999)])]),
        memory=mem,
    )
    result = skill.step(ctx)
    assert not isinstance(result.action, PickUp)
    assert mem["hunt_looted"] == [CORPSE]


def test_loot_attempts_bounded_gives_up_transiently_on_a_bounce():
    # A `Drop` that never actually lands (the item stays in the corpse forever,
    # simulating a bounced deposit) must not retry forever.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 5)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    # obs never changes — the gold item stays put in the corpse forever, as if
    # every PickUp/Drop attempt silently bounced.
    for _ in range(2 * skill.loot_attempts + 2):
        skill.step(ctx)
        if mem.get("hunt_queue", [CORPSE]) == []:
            break
    assert mem["hunt_queue"] == []
    assert CORPSE in mem["hunt_giveup"]
    assert CORPSE not in mem.get("hunt_looted", [])  # a bounce isn't "fully looted"


def test_abandon_mid_lift_completes_drop_before_retiring_corpse():
    # A corpse that vanishes from obs between the PickUp tick and the Drop
    # tick must not be abandoned with the item still lifted onto the server
    # cursor — ServUO rejects all further lifts while it's held.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 5)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    lift = skill.step(ctx)
    assert isinstance(lift.action, PickUp)
    assert mem["hunt_held"] == GOLD_SERIAL

    # The corpse itself disappears (decayed, or the bridge stopped
    # reporting it) before the Drop tick ever runs.
    ctx.obs = _obs([_backpack()], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    result = None
    for _ in range(skill.corpse_find_timeout + 1):
        result = skill.step(ctx)
        if isinstance(result.action, Drop):
            break
    assert isinstance(result.action, Drop)
    assert result.action.serial == GOLD_SERIAL
    assert result.action.container == BACKPACK
    assert "hunt_held" not in mem

    # Retirement (transient give-up, not permanent) completes on a later tick.
    skill.step(ctx)
    assert CORPSE in mem["hunt_giveup"]
    assert mem["hunt_queue"] == []


def test_abandon_when_backpack_momentarily_missing_still_completes_drop():
    # A `Drop` that would bounce because the backpack isn't visible this
    # exact tick must not silently discard `hunt_held` either — the item
    # stays tracked until the backpack reappears and the Drop actually fires.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 5)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    lift = skill.step(ctx)
    assert isinstance(lift.action, PickUp)
    assert mem["hunt_held"] == GOLD_SERIAL

    ctx.obs = _obs([_corpse(), contents], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    waiting = skill.step(ctx)
    assert waiting.action is None
    assert mem["hunt_held"] == GOLD_SERIAL  # still tracked, not discarded

    ctx.obs = _obs([_backpack(), _corpse(), contents], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    result = skill.step(ctx)
    assert isinstance(result.action, Drop)
    assert result.action.serial == GOLD_SERIAL
    assert result.action.container == BACKPACK
    assert "hunt_held" not in mem


# --- reward: observed pack gains only ---------------------------------------------


def test_reward_pays_only_on_confirmed_pack_gain():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 15)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    lift = skill.step(ctx)
    assert lift.reward == 0.0  # lifting alone confirms nothing yet
    place = skill.step(ctx)
    assert place.reward == 0.0  # issuing the Drop isn't confirmation either

    # Simulate the server: gold moved from the corpse into the pack.
    ctx.obs = _obs([_backpack(), _pack_gold(GOLD_SERIAL, 15), _corpse()],
                   corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    confirmed = skill.step(ctx)
    # Confirmed on the very tick the corpse scan comes up empty (queue drains,
    # phase resets, and this skill falls through to Combat) — banked across
    # that observation-lag gap, not dropped.
    assert confirmed.reward == 15.0
    assert mem["hunt_queue"] == []

    # No double-payment on a later tick where nothing changed.
    again = skill.step(ctx)
    assert again.reward == 0.0


def test_reward_pays_gain_confirmed_one_observation_after_drain():
    # The pack total can lag the corpse-scan-comes-up-empty tick by more than
    # the usual single tick (an in-flight Drop that hasn't landed yet) — the
    # settle window must still catch and pay it, not lose it when the phase
    # has already reset to engage.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 15)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    skill.step(ctx)  # PickUp
    skill.step(ctx)  # Drop

    # Corpse scan comes up empty, but the pack hasn't caught up yet this tick.
    ctx.obs = _obs([_backpack(), _corpse()], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    drained = skill.step(ctx)
    assert drained.reward == 0.0
    assert mem["hunt_queue"] == []
    assert mem["hunt_phase"] == "engage"

    # One observation later the pack finally shows the gain.
    ctx.obs = _obs([_backpack(), _pack_gold(GOLD_SERIAL, 15)], corpse_of=[])
    late = skill.step(ctx)
    assert late.reward == 15.0

    again = skill.step(ctx)
    assert again.reward == 0.0  # not double-paid


def test_reward_pays_gain_confirmed_two_observations_after_drain():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 15)
    ctx = _ctx(_obs([_backpack(), _corpse(), contents],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    skill.step(ctx)  # PickUp
    skill.step(ctx)  # Drop

    ctx.obs = _obs([_backpack(), _corpse()], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    drained = skill.step(ctx)
    assert drained.reward == 0.0

    # Still nothing one tick later either.
    still_nothing = skill.step(ctx)
    assert still_nothing.reward == 0.0

    # Two observations after the drain, the pack finally catches up — right
    # at the edge of the settle window (loot_reward_settle_ticks == 2).
    ctx.obs = _obs([_backpack(), _pack_gold(GOLD_SERIAL, 15)], corpse_of=[])
    late = skill.step(ctx)
    assert late.reward == 15.0


def test_loot_reward_defers_baseline_until_backpack_visible():
    # If the backpack is momentarily absent from obs on the very first loot
    # tick, the baseline must not be captured as 0 — that would falsely pay
    # out whatever valuables the pack already held once it reappears.
    skill = Hunt()
    mem = {"hunt_attacked": [MOB], "hunt_queue": [CORPSE], "hunt_loot_stage": "loot"}
    contents = _corpse_gold(CORPSE, GOLD_SERIAL, 5)
    ctx = _ctx(_obs([_corpse(), contents], corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)]), memory=mem)
    first = skill.step(ctx)
    assert first.reward == 0.0
    assert "hunt_val_start" not in mem  # baseline deferred, not falsely set to 0

    # Backpack reappears already holding pre-existing valuables (not a fresh
    # gain from this loot run) — must not be paid once the baseline is set.
    ctx.obs = _obs([_backpack(), _pack_gold(0x999, 20), _corpse(), contents],
                   corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB)])
    second = skill.step(ctx)
    assert second.reward == 0.0
    assert mem["hunt_val_start"] == 20


def test_reward_never_pays_for_the_attack_itself():
    ctx = _ctx(_obs([_backpack()], mobiles=[_mongbat()]))
    skill = Hunt()
    skill.step(ctx)  # WarMode
    attack = skill.step(ctx)  # Attack
    assert isinstance(attack.action, Attack)
    assert attack.reward == 0.0


# --- multi-cycle: the queue drains sequentially, one corpse at a time -----------


def test_multiple_queued_corpses_drain_one_after_another():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB, MOB2], "hunt_queue": [CORPSE, CORPSE2]}
    c1 = _corpse(serial=CORPSE, pos=Position(101, 100, 0))
    c2 = _corpse(serial=CORPSE2, pos=Position(102, 100, 0))
    ctx = _ctx(_obs([_backpack(), c1, c2],
                    corpse_of=[CorpseLink(corpse=CORPSE, killed=MOB), CorpseLink(corpse=CORPSE2, killed=MOB2)]),
              memory=mem)
    # Drain corpse 1 (empty — no whitelisted loot) via repeated ticks; the
    # queue should move on to corpse 2 without needing a fresh kill.
    for _ in range(3 + skill.open_settle_ticks * skill.open_attempts):
        skill.step(ctx)
        if mem.get("hunt_queue") == [CORPSE2] or CORPSE not in mem.get("hunt_queue", []):
            break
    # Neither corpse ever shows any contents — a never-opened give-up (see
    # test_open_retries_use_when_nothing_shows_up_at_all), not permanent —
    # but either way the queue must still move on to corpse 2 on its own.
    assert CORPSE in mem["hunt_giveup"]
    assert mem["hunt_queue"] == [CORPSE2]


# --- bounding: no unbounded memory growth, ever -----------------------------------


def test_hunt_giveup_dict_bounded_to_max_tracked():
    skill = Hunt()
    mem = {"hunt_attacked": [MOB]}
    ctx = _ctx(_obs([_backpack()]), memory=mem)
    for i in range(skill.max_tracked + 5):
        serial = 0x1000 + i
        far = _corpse(serial=serial, distance=CORPSE_REACH + 5, pos=Position(200, 100, 0))
        mem["hunt_queue"] = [serial]
        ctx.obs = _obs([_backpack(), far])
        for _ in range(skill.stall_limit + 1):
            skill.step(ctx)
    # Mirrors the same defensive cap `hunt_attacked`/`hunt_looted` already get.
    assert len(mem["hunt_giveup"]) <= skill.max_tracked


# --- constants / defaults mirror the established conventions ---------------------


def test_giveup_cooldown_matches_blacksmith_market_convention():
    assert Hunt.giveup_cooldown_ticks == 30
