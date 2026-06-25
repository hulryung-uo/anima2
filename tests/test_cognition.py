"""The slow cognition loop: LLM goal parsing, speech queueing, non-blocking wrap."""

import threading

from anima2.cognition import LLMCognition, ThreadedCognition
from anima2.contract import Observation, PlayerView, Position, Say
from anima2.llm import StubLLMClient
from anima2.persona import Persona
from anima2.skills import SpeakPending
from anima2.skills.base import Goal, SkillContext


def _ctx(goal=None, memory=None) -> SkillContext:
    obs = Observation(player=PlayerView(serial=1, pos=Position(3724, 2212, 20), hits=80, hits_max=80))
    return SkillContext(obs=obs, persona=Persona(name="Grimm", title="a miner"),
                        goal=goal, memory=memory if memory is not None else {})


def test_llm_cognition_parses_goto_and_queues_speech():
    client = StubLLMClient('{"goal": "goto", "x": 3716, "y": 2204, "say": "Off to the mine."}')
    ctx = _ctx()
    goal = LLMCognition(client).reconsider(ctx)
    assert goal is not None and goal.kind == "goto"
    assert (goal.params["target"].x, goal.params["target"].y) == (3716, 2204)
    assert ctx.memory["pending_say"] == "Off to the mine."
    # The persona + situation actually reached the model.
    assert "Grimm" in client.calls[0][0]


def test_llm_cognition_idle_clears_goal():
    ctx = _ctx(goal=Goal(kind="goto", params={}))
    goal = LLMCognition(StubLLMClient('{"goal": "idle"}')).reconsider(ctx)
    assert goal is None


def test_llm_cognition_tolerates_garbage():
    ctx = _ctx(goal=Goal(kind="goto", params={}))
    goal = LLMCognition(StubLLMClient("sorry, I cannot help")).reconsider(ctx)
    assert goal is ctx.goal  # unparseable → leaves the current goal untouched


def test_speak_pending_drains_queue():
    ctx = _ctx(memory={"pending_say": "Heavy ore today."})
    skill = SpeakPending()
    assert skill.can_run(ctx)
    res = skill.step(ctx)
    assert isinstance(res.action, Say) and res.action.text == "Heavy ore today."
    assert not skill.can_run(ctx)  # drained


def test_threaded_cognition_never_blocks():
    gate = threading.Event()
    target_goal = Goal(kind="goto", params={"target": Position(1, 2, 0)})

    class SlowInner:
        def reconsider(self, ctx):
            gate.wait(2.0)  # block until the test releases us
            return target_goal

    cog = ThreadedCognition(SlowInner())
    ctx = _ctx(goal=None)
    # First call kicks off the background thread and returns immediately (current goal).
    assert cog.reconsider(ctx) is None
    gate.set()
    # Poll for the background result to land (no busy-blocking in reconsider).
    got = None
    for _ in range(200):
        got = cog.reconsider(ctx)
        if got is target_goal:
            break
        threading.Event().wait(0.01)
    assert got is target_goal
