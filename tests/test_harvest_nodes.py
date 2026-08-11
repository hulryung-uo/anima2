"""Which TILE a harvester swings at — node cycling, stale nodes, and the readout.

Every test here fails on the code as it stood on 2026-08-12. The three defects
they pin were found by an adversarial review of the miner's five-run freeze
(audit §34) and share one shape: the miner had exactly one tile it would ever
target, and nothing in the telemetry could say so.

  1. `harvest_idx` advanced only on cliloc 500493 — LUMBERJACKING's
     "no resources" message. `Mine` says 503040. So `Mine` never cycled at all.
  2. A BLIND relocation (the surveyed pool spent) left the condemned stand's
     node list installed, so every swing from the new stand answered "That is
     too far away" about rock 12+ tiles behind us.
  3. `find_mine_spots` emitted its nodes in raster order, making `nodes[0]` the
     far CORNER of the reach box — which falls outside ServUO's MaxRange=2 the
     moment relocation accepts its documented one-tile-short arrival.
"""



from anima2.contract import (
    ItemView,
    JournalEntry,
    Observation,
    PlayerView,
    Position,
    SkillView,
    TargetCursor,
    TargetGround,
)
from anima2.persona import Persona
from anima2.skills import Chop, Fish, Mine
from anima2.skills.base import SkillContext
from anima2.skills.harvest import NODE_DEPLETED_CLILOC

PICKAXE = 0x0E86
BACKPACK = 0x40001453
CURSOR = TargetCursor(target_type=1, cursor_id=1, cursor_flag=0)


def _pack(graphic=PICKAXE):
    return ItemView(serial=0x222, graphic=graphic, amount=1, pos=Position(),
                    container=BACKPACK, layer=0, distance=0)


def _swings(mem, verdict, *, count, pos=(100, 100), skill=None, tool=PICKAXE):
    """Drive `count` full swing→cursor→verdict cycles and return the tiles targeted.

    Mirrors the real cadence: the verdict cliloc lands on its own tick (that is
    where `step` reads it), and the NEXT cursor tick shows which node the skill
    moved to — so the returned list is exactly "what did it aim at, in order".
    """
    sk = skill or Mine()
    item = _pack(tool)

    def obs(pending=None, journal=()):
        return Observation(
            player=PlayerView(serial=1, pos=Position(*pos, 0)),
            items=[item],
            skills=[SkillView(id=sk.skill_id, value=35.0, base=35.0, cap=100.0, lock=0)],
            pending_target=pending,
            new_journal=list(journal),
        )

    def tick(pending=None, journal=()):
        return sk.step(SkillContext(obs=obs(pending, journal),
                                    persona=Persona(name="Grimm"), memory=mem))

    aimed = []
    entry = [JournalEntry(0, "System", "", 0, 0, cliloc=verdict)] if verdict else []
    for _ in range(count):
        act = tick(pending=CURSOR).action
        if isinstance(act, TargetGround):
            aimed.append((act.x, act.y))
        tick(journal=entry)
    return aimed


# --- 1. cycling: the node the shard just condemned is not the node to retry ----------

def test_mine_cycles_to_the_next_node_when_the_shard_says_there_is_no_metal():
    """THE defect. 503040 is mining's own `NoResourcesMessage`; before the fix it
    advanced nothing, so a stand with 15 surveyed tiles drained exactly one."""
    nodes = [(99, 99, 0, 0), (101, 101, 0, 0), (98, 100, 0, 0)]
    mem: dict = {"harvest_nodes": nodes}
    aimed = _swings(mem, 503040, count=3)
    assert aimed == [(99, 99), (101, 101), (98, 100)]  # was [(99,99)] * 3


def test_mine_cycles_off_a_node_it_cannot_even_target():
    """"That is too far away" (500446) is the stale-node wedge's own signature,
    and "You can't mine that" (501862) a tile that will NEVER pay. Retrying either
    forever is the same bug wearing a different cliloc."""
    for cliloc in sorted(Mine.invalid_target_clilocs):
        mem: dict = {"harvest_nodes": [(99, 99, 0, 0), (101, 101, 0, 0)]}
        assert _swings(mem, cliloc, count=2) == [(99, 99), (101, 101)], cliloc


def test_a_full_pack_does_not_cycle_the_node():
    """The pack is OUR problem, not the tile's — the dig succeeded server-side.
    Cycling here would walk a live vein because we ran out of room on it."""
    mem: dict = {"harvest_nodes": [(99, 99, 0, 0), (101, 101, 0, 0)]}
    assert _swings(mem, 1010481, count=3) == [(99, 99)] * 3


def test_a_productive_reply_does_not_cycle_the_node():
    """The bank still yields — stay on it. `503043` is "you loosen some rocks but
    fail", which is proof of life, not a verdict against the tile."""
    mem: dict = {"harvest_nodes": [(99, 99, 0, 0), (101, 101, 0, 0)]}
    assert _swings(mem, 503043, count=3) == [(99, 99)] * 3


def test_chop_is_unchanged_by_the_widened_cycling_rule():
    """A woodsman's behaviour must not move: `Chop` leaves both
    `no_resource_clilocs` and `invalid_target_clilocs` empty, so the derived set
    collapses back to exactly the one cliloc it always used."""
    assert Chop().node_exhausted_clilocs == {NODE_DEPLETED_CLILOC}
    grove = [(99, 99, 0, 0x0CCA), (101, 101, 0, 0x0CCB)]
    mem: dict = {"harvest_nodes": grove}
    assert _swings(mem, NODE_DEPLETED_CLILOC, count=2,
                   skill=Chop(), tool=0x0F43) == [(99, 99), (101, 101)]


def test_fish_cycles_off_water_that_is_not_biting():
    """`Fish` DOES change, deliberately and in the same direction: 503172 is
    "the fish don't seem to be biting here", which condemns that water tile."""
    mem: dict = {"harvest_nodes": [(99, 99, 0, 0), (101, 101, 0, 0)]}
    assert _swings(mem, 503172, count=2, skill=Fish(), tool=0x0DBF) == [
        (99, 99), (101, 101)]


def test_node_exhausted_clilocs_is_derived_and_never_a_fourth_set():
    """Single-source discipline, as a property: the cycling rule is a UNION of the
    sets each subclass already declares, so a new failure cliloc cannot be added
    to one side of the decision and forgotten on the other — the audit's headline
    defect class, and the exact shape of the 500493-vs-503040 bug itself."""
    for cls in (Mine, Chop, Fish):
        sk = cls()
        assert sk.node_exhausted_clilocs == (
            sk.no_resource_clilocs | sk.invalid_target_clilocs | {NODE_DEPLETED_CLILOC})
        # A full pack is about US and must never be in there.
        assert not (sk.node_exhausted_clilocs & sk.pack_full_clilocs)


def test_cycling_reaches_a_second_ore_bank_without_relocating():
    """Why the cycling is worth a live day. ServUO banks are 8x8 on a (0,0) grid,
    so a stand near a boundary reaches TWO of them — 4 of the forge pool's 12
    stands do. With the index frozen the miner drained bank A and walked away
    from a full bank one tile further on; the pool's own arithmetic is 12 banks
    reachable out of 14 present."""
    # Standing at x=103: reach 2 spans x 101..105, and the bank edge is at 104.
    nodes = [(102, 100, 0, 0), (104, 100, 0, 0)]
    mem: dict = {"harvest_nodes": nodes}
    aimed = _swings(mem, 503040, count=2, pos=(103, 100))
    assert {(x // 8, y // 8) for x, y in aimed} == {(12, 12), (13, 12)}
    assert mem["harvest_banks_touched"] == {(12, 12), (13, 12)}


# --- 2. the blind hop that kept aiming at the ground it had just condemned -----------

def _relocating(mem, *, target, nodes):
    """A `Mine` mid-relocation toward `target` with `nodes` installed and NO
    pending replacement — i.e. the blind compass hop taken once the surveyed pool
    is spent, which is the state the wedge needs."""
    mem.update({
        "harvest_nodes": nodes,
        "harvest_idx": 3,
        "harvest_relocating": True,
        "harvest_relocate_target": target,
        # None, not a full window: `_start_relocate` clears the window as it sets
        # off, so a relocation in flight always carries an empty one. Seeding a
        # full one instead makes the arrival tick fall through and immediately
        # relocate AGAIN, which is a fixture artefact and not the behaviour.
        "harvest_recent_stuck": None,
    })
    return Mine()


def test_a_blind_relocation_drops_nodes_it_walked_out_of_reach_of():
    """The wedge: `RELOCATE_OFFSETS` hops 12 tiles, the old stand's rock stays
    installed, and `_current_node` goes on naming tiles at chebyshev 12+. Every
    swing answers "too far away" — a verdict that is a FAILURE, so the window
    fills, and the miner relocates again, forever, mining nothing.

    The stall-giveup branch two lines below always dropped them, for a reason
    that reads identically here: the old nodes belong to the ground just
    condemned. Two arrival paths, one of them wrong."""
    mem: dict = {}
    mine = _relocating(mem, target=(112, 100), nodes=[(99, 99, 0, 0), (101, 101, 0, 0)])
    obs = Observation(player=PlayerView(serial=1, pos=Position(112, 100, 0)),
                      items=[_pack()])
    mine.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))
    assert not mem.get("harvest_relocating")          # arrived
    assert "harvest_nodes" not in mem                 # ...and let the dead rock go
    assert "harvest_idx" not in mem
    # Which means the next swing PROBES the new ground instead of naming the old.
    aimed = _swings(mem, 503040, count=1, pos=(112, 100))
    assert aimed and max(abs(aimed[0][0] - 112), abs(aimed[0][1] - 100)) <= 2


def test_a_short_hop_keeps_the_nodes_that_are_still_in_reach():
    """The guard is deliberately NOT unconditional. A hop that leaves part of the
    cluster swingable keeps it — cycling walks past whatever fell out of range —
    and that matters most for a woodsman, whose exact tree nodes cannot be
    re-probed blind at all: dropping those would trade one wedge for another."""
    mem: dict = {}
    mine = _relocating(mem, target=(102, 100), nodes=[(99, 99, 0, 0), (101, 101, 0, 0)])
    obs = Observation(player=PlayerView(serial=1, pos=Position(102, 100, 0)),
                      items=[_pack()])
    mine.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))
    assert mem["harvest_nodes"] == [(99, 99, 0, 0), (101, 101, 0, 0)]


def test_an_arriving_pool_spot_still_installs_its_own_surveyed_rock():
    """The guard must not fire on the path that DOES carry replacements — the
    surveyed-pool arrival, which is the whole point of the pool."""
    mem: dict = {"harvest_pending_nodes": [(151, 89, 12, 0)]}
    mine = _relocating(mem, target=(150, 90), nodes=[(99, 99, 0, 0)])
    obs = Observation(player=PlayerView(serial=1, pos=Position(150, 90, 0)),
                      items=[_pack()])
    mine.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))
    assert mem["harvest_nodes"] == [(151, 89, 12, 0)]
    assert mem["harvest_idx"] == 0


# --- 3. the readout: which failure, and how many banks -------------------------------

def test_the_stuck_cause_split_partitions_the_windows_failures():
    """`win=` collapses five distinguishable verdicts into a single `1`, which is
    why five audit sections could describe the dead tail without ever telling an
    exhausted bank apart from a tile the miner could not hit. A PARTITION: the
    counts sum to the window's own `1`s, so the arithmetic is checkable."""
    mem: dict = {}
    for cliloc in (503040, 503040, 500446, 501862, 1010481):
        _swings(mem, cliloc, count=1)
    by = mem["harvest_stuck_by_cause"]
    assert by == {"nores": 2, "inval": 2, "packfull": 1}
    assert sum(by.values()) == sum(mem["harvest_recent_stuck"])


def test_a_tick_carrying_two_verdicts_is_charged_to_exactly_one_cause():
    """What makes it a partition rather than a tally. The body batches journal
    entries, so one observation really can carry both "there is no metal here"
    and "that is too far away" — and a tally would then report more failures than
    the window ever recorded, which is precisely the arithmetic a reader uses to
    check the split is trustworthy."""
    mem: dict = {}
    sk = Mine()
    obs = Observation(
        player=PlayerView(serial=1, pos=Position(100, 100, 0)),
        items=[_pack()],
        skills=[SkillView(id=45, value=35.0, base=35.0, cap=100.0, lock=0)],
        new_journal=[JournalEntry(0, "System", "", 0, 0, cliloc=503040),
                     JournalEntry(0, "System", "", 0, 0, cliloc=500446)],
    )
    sk.step(SkillContext(obs=obs, persona=Persona(name="Grimm"), memory=mem))
    assert mem["harvest_stuck_by_cause"] == {"nores": 1}
    assert sum(mem["harvest_stuck_by_cause"].values()) == sum(mem["harvest_recent_stuck"]) == 1


def test_a_productive_reply_is_charged_to_no_cause():
    mem: dict = {}
    _swings(mem, 503043, count=3)
    assert not mem.get("harvest_stuck_by_cause")
    assert sum(mem["harvest_recent_stuck"]) == 0


def test_only_mining_counts_banks_because_only_minings_grid_is_pinned():
    """ServUO gives every harvest definition its own `BankWidth`/`BankHeight`;
    only `oreAndStone`'s 8x8 is pinned by a test here, so wood and water stay
    unmodelled rather than guessed."""
    assert Mine.bank_cell_size == 8
    assert Chop.bank_cell_size == 0 and Fish.bank_cell_size == 0
    mem: dict = {"harvest_nodes": [(99, 99, 0, 0x0CCA)]}
    _swings(mem, NODE_DEPLETED_CLILOC, count=2, skill=Chop(), tool=0x0F43)
    assert "harvest_banks_touched" not in mem


# --- 4. the surveyed stand's first node has to survive a one-tile-short arrival ------

def test_surveyed_nodes_are_nearest_first_and_survive_the_arrival_tolerance():
    """`_relocate_step` accepts arrival ONE tile short of a surveyed stand on
    purpose (a wall-side stand is exactly where the greedy mover gets deflected).
    Raster order put `nodes[0]` at the far corner of the reach box — chebyshev 2
    from the stand, so chebyshev 3 from a deflected arrival, past ServUO's
    MaxRange=2. The first swing of the relocation the pool exists for answered
    "That is too far away"."""
    from anima2.uomap import find_mine_spots
    from anima2.village import LUMBER_MAP, TRADE_MINE_SPOT

    spots = find_mine_spots(LUMBER_MAP, *TRADE_MINE_SPOT)
    assert len(spots) == 12
    for (sx, sy), nodes in spots:
        d = max(abs(nodes[0][0] - sx), abs(nodes[0][1] - sy))
        assert d <= 1, ((sx, sy), nodes[0], d)  # was 2 for all 12
        assert all(
            max(abs(nodes[0][0] - sx + ox), abs(nodes[0][1] - sy + oy)) <= 2
            for ox in (-1, 0, 1) for oy in (-1, 0, 1)
        ), "must stay in MaxRange=2 from every tile a deflected arrival accepts"


def test_the_pool_still_covers_every_stand_and_its_second_bank():
    """The re-sort must not lose coverage: same 12 stands, same 12 stand banks,
    and the 14 distinct banks their node lists span (the 2 extra being what
    cycling is for) all still present."""
    from anima2.uomap import find_mine_spots
    from anima2.village import LUMBER_MAP, TRADE_MINE_SPOT

    spots = find_mine_spots(LUMBER_MAP, *TRADE_MINE_SPOT)
    banks = {(n[0] // 8, n[1] // 8) for _, nodes in spots for n in nodes}
    assert len({(s[0] // 8, s[1] // 8) for s, _ in spots}) == 12
    assert len(banks) == 14
    assert min(max(abs(a[0][0] - b[0][0]), abs(a[0][1] - b[0][1]))
               for i, a in enumerate(spots) for b in spots[i + 1:]) >= 8
