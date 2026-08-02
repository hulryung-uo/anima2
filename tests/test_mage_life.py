"""MageLife.decide_mode — the mage's autonomous hunt <-> resupply switch. A mage stops
being able to fight for a different reason than a warrior (an empty reagent pouch, not a
lost blade), so it gets its own rule over the same live-verified orchestrator."""

from anima2.contract import ItemView, Observation, PlayerView, Position
from anima2.persona import Persona
from anima2.skills.harvest import BACKPACK_LAYER
from anima2.skills.hunt import GOLD_GRAPHIC
from anima2.skills.mage import SULFUROUS_ASH_GRAPHIC
from anima2.mage_life import (
    BANK_RESERVE,
    LOW_REAGENTS,
    REAGENT_BATCH_COST,
    MageLife,
    decide_mode,
)

PLAYER = 1
BP = 0x50
ROUTES = {"mage_vendor_spot": ((10, 10),), "banker_spot": ((10, 10),)}


def _item(serial, graphic, amount=1, *, container=BP, layer=0, distance=0):
    return ItemView(serial=serial, graphic=graphic, amount=amount, pos=Position(),
                    container=container, layer=layer, distance=distance)


def _backpack():
    return _item(BP, 0x0E75, container=PLAYER, layer=BACKPACK_LAYER)


def _ash(n):
    return _item(0x900, SULFUROUS_ASH_GRAPHIC, n)


def _gold(n):
    return _item(0x901, GOLD_GRAPHIC, n)


def _ground_gold(n, distance=2):
    """A crafter's delivered purse: a world item (container is None)."""
    return _item(0x902, GOLD_GRAPHIC, n, container=None, distance=distance)


def _obs(items, *, dead=False):
    return Observation(player=PlayerView(serial=PLAYER, pos=Position(5, 5, 0), dead=dead),
                       items=list(items))


def test_a_supplied_mage_hunts():
    obs = _obs([_backpack(), _ash(50), _gold(100)])
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


def test_an_empty_pouch_sends_the_mage_shopping():
    # Reagents are the caster's blade: without ash, CastAttack is inert.
    obs = _obs([_backpack(), _ash(LOW_REAGENTS - 1), _gold(REAGENT_BATCH_COST)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_reagent")


def test_a_broke_or_unrouted_mage_keeps_hunting_instead_of_stalling():
    poor = _obs([_backpack(), _ash(0), _gold(REAGENT_BATCH_COST - 1)])
    assert decide_mode(poor, dict(ROUTES)) == ("hunt", None)
    unrouted = _obs([_backpack(), _ash(0), _gold(500)])
    assert decide_mode(unrouted, {}) == ("hunt", None)


def test_a_delivered_purse_on_the_ground_is_collected():
    # The production pipeline closing into the mage's own life: a crafter dropped its
    # earnings here, so go pick them up — that gold buys the next reagent batch.
    obs = _obs([_backpack(), _ash(50), _ground_gold(300)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "fetch_gold")


def test_reagents_outrank_collecting_a_purse():
    # Both true at once: buy the ash it can already afford before walking to the purse.
    obs = _obs([_backpack(), _ash(0), _gold(REAGENT_BATCH_COST), _ground_gold(300)])
    assert decide_mode(obs, dict(ROUTES)) == ("economy", "buy_reagent")


def test_surplus_gold_banks_but_only_when_nothing_is_needed():
    rich = _obs([_backpack(), _ash(50), _gold(BANK_RESERVE + 1)])
    assert decide_mode(rich, dict(ROUTES)) == ("economy", "bank_gold")
    # ...and a dry pouch still wins over banking.
    dry = _obs([_backpack(), _ash(0), _gold(BANK_RESERVE + 1)])
    assert decide_mode(dry, dict(ROUTES)) == ("economy", "buy_reagent")


def test_a_dead_mage_yields_to_recover_death():
    obs = _obs([_backpack(), _ash(0), _gold(500)], dead=True)
    assert decide_mode(obs, dict(ROUTES)) == ("hunt", None)


class _MockBody:
    connected = True
    ready = {"player": {"serial": PLAYER}}

    def __init__(self, seq):
        self._it = iter(seq)
        self._last = None

    def observe(self):
        try:
            self._last = next(self._it)
        except StopIteration:
            pass
        return self._last

    def act(self, action):
        pass


def test_mage_life_reuses_the_warrior_orchestrator_with_its_own_rule():
    from anima2.warrior_life import WarriorLife

    # Structure is the live-verified warrior orchestrator; only profession + rule differ.
    assert issubclass(MageLife, WarriorLife)
    life = MageLife(body=_MockBody([_obs([_backpack(), _ash(50)])]),
                    persona=Persona(name="Elara"), routes=dict(ROUTES))
    assert set(life.econ_agent.planner.capability_ids) == {"buy_reagent", "fetch_gold", "bank_gold"}
    # The hunt side is the mage's own: it kites, then casts, then hunts — and its hunt
    # is `ArmedHunt`, which refuses to close to melee once it can no longer cast.
    names = [type(s).__name__ for s in life.hunt_agent.planner.skills]
    assert names.index("KeepDistance") < names.index("CastAttack") < names.index("ArmedHunt")
    # Separate memories (the warrior's live-caught requirement) still hold.
    assert life.hunt_agent.memory is not life.econ_agent.memory


def test_the_switch_keeps_the_warrior_s_hysteresis():
    # A transient "needs something" blip must not yank the mage off a fight mid-action;
    # the inherited grace filters it exactly as it does for the warrior.
    from anima2.warrior_life import ECON_GRACE

    dry = _obs([_backpack(), _ash(0), _gold(REAGENT_BATCH_COST)])
    life = MageLife(body=_MockBody([dry] * (ECON_GRACE + 2)),
                    persona=Persona(name="Elara"), routes=dict(ROUTES))
    for _ in range(ECON_GRACE - 1):
        life.tick()
        assert life.mode == "hunt"
    life.tick()
    assert life.mode == "economy" and life.target_cap == "buy_reagent"


# --- LLM steering (audit #8): choose only among what the rule admits ----------------

def _overlap_obs():
    # Both buy_reagent AND fetch_gold admissible at once: ash low, gold covers a batch
    # but sits under the fetch cap, a purse within pickup reach.
    return _obs([_backpack(), _ash(LOW_REAGENTS - 1), _gold(REAGENT_BATCH_COST),
                 _ground_gold(140, distance=2)])


def test_decide_candidates_and_decide_mode_are_one_predicate_set():
    from anima2.mage_life import decide_candidates

    obs = _overlap_obs()
    cands = decide_candidates(obs, dict(ROUTES))
    assert cands == ["buy_reagent", "fetch_gold"]
    assert decide_mode(obs, dict(ROUTES)) == ("economy", cands[0])


class _ScriptedLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return self.reply


def _steered_life(reply):
    from anima2.warrior_life import _LifeClient

    life = MageLife(body=_MockBody([_overlap_obs()] * 20),
                    persona=Persona(name="Elara"), routes=dict(ROUTES))
    llm = _ScriptedLLM(reply)
    client = _LifeClient(life, llm)
    # Drive ticks until the hysteresis commits and candidates are published.
    for _ in range(10):
        life.tick()
    return life, client, llm


def test_the_llm_choice_steers_within_the_candidate_set():
    life, client, llm = _steered_life("fetch_gold")
    assert life.candidates == ["buy_reagent", "fetch_gold"]
    out = client.complete("", "")
    assert '"capability":"fetch_gold"' in out          # the SECOND candidate — steering
    assert llm.calls == 1
    assert life.steering_log[-1] == (("buy_reagent", "fetch_gold"), "fetch_gold", True)


def test_an_answer_outside_the_set_falls_back_to_the_rules_choice():
    # Closed vocabulary: the model cannot mint an option the rule did not admit.
    life, client, llm = _steered_life("go murder the vendor and take everything")
    out = client.complete("", "")
    assert '"capability":"buy_reagent"' in out         # rule's own first choice
    assert life.steering_log[-1][2] is False           # recorded as NOT an LLM pick


def test_a_single_candidate_never_consults_the_llm():
    # The fast loop is not the LLM's place: one admissible branch = scripted.
    from anima2.warrior_life import _LifeClient

    only_buy = _obs([_backpack(), _ash(LOW_REAGENTS - 1), _gold(REAGENT_BATCH_COST)])
    life = MageLife(body=_MockBody([only_buy] * 20),
                    persona=Persona(name="Elara"), routes=dict(ROUTES))
    llm = _ScriptedLLM("fetch_gold")
    client = _LifeClient(life, llm)
    for _ in range(10):
        life.tick()
    out = client.complete("", "")
    assert '"capability":"buy_reagent"' in out
    assert llm.calls == 0
