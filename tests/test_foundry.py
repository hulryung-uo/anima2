

def test_an_offline_life_day_scores_a_knob_against_banked_gold():
    """The searcher end of the tuning channel, which `docs/AUTONOMY-ROADMAP.md` §E has
    been blocked on: `foundry/eval.py` stages through `GmControl` and measures a bare
    `Agent` with one work skill, so nothing has ever scored a LIFE, and every evaluation
    cost shard time.

    `MockBanker` (audit §64) made a Life's economy leg runnable offline, so a knob can be
    scored against banked gold for free. It builds through `life_runner.build_tuned_life`
    — the seam the six production runners use — so a searched value travels the channel a
    shard would and is refused by the same allowlist.
    """
    from anima2.foundry.life_eval import LifeTrial, run_life_trial, sweep
    from anima2.warrior_life import WarriorLife

    scored = sweep(WarriorLife, "bank_reserve", (0, 400, 1200), ticks=400, gold=2000)

    # The day has to be HEALTHY for the number to mean anything: a banked 0 with no frame
    # is a broken fixture, not a bad knob.
    assert all(r.frames and r.achieved == r.frames for r in scored.values()), scored

    # The axis moves the fitness, monotonically, in the direction its semantics promise —
    # the reserve is what STAYS in the pack.
    banked = [scored[v].banked for v in (0, 400, 1200)]
    assert banked == sorted(banked, reverse=True), banked
    assert len(set(banked)) == 3, banked
    for value, result in scored.items():
        assert result.pack_gold == value, (value, result)
        assert result.banked + result.pack_gold == 2000, result

    # An axis the Life does not declare is REFUSED at construction, exactly as on a shard.
    import pytest
    with pytest.raises(Exception):
        run_life_trial(LifeTrial(life_cls=WarriorLife, knobs={"not_a_knob": 1}, ticks=5))


def test_the_offline_day_cannot_search_a_damage_mediated_knob():
    """The harness's measured limit, kept as a test so it is not quietly forgotten.

    Four of the five warrior knobs scored identically across every value: they bite only
    when something goes wrong, and what goes wrong live is the warrior being HURT.
    `MockBody` has no damage model, so that state is unreachable — and injecting the wound
    instead drops the Life below `heal_below_fraction`, where §56's rule refuses the
    economy leg outright.

    If this ever starts failing, the mock has grown a damage model and the scope note in
    `life_eval`'s docstring needs rewriting rather than deleting.
    """
    from anima2.foundry.life_eval import sweep
    from anima2.warrior_life import WarriorLife

    prey = ((1, 1), (-1, -1), (1, -1))
    for axis, values in (("bank_return_reach", (0, 2)),
                         ("econ_grace", (2, 20)),
                         ("wander_leash", (1, 8))):
        scored = sweep(WarriorLife, axis, values, ticks=400, prey=prey)
        assert len({r.banked for r in scored.values()}) == 1, (axis, scored)
