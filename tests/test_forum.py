"""Forum posting: in-character composition + post wiring (offline)."""

from types import SimpleNamespace

from anima2.forum import ForumClient, compose_post, post_day
from anima2.memory import Episode, EpisodicMemory
from anima2.persona import Persona


def test_compose_post_summarizes_a_working_day():
    p = Persona(name="Grimm", title="a miner", interests="mining ore, smelting")
    eps = [Episode(tick=i, kind="skill", summary="mine", reward=0.1) for i in range(5)]
    title, content = compose_post(p, eps, job="miner")
    assert "Grimm" in title and "miner" in title
    assert content and "0.5" in content  # the day's output


def test_compose_post_handles_a_quiet_day():
    title, content = compose_post(Persona(name="Sera", title="a wanderer"), [], job="townsfolk")
    assert "Sera" not in title or content  # produces something
    assert "quiet" in content.lower()


class _FakeClient:
    configured = True

    def __init__(self) -> None:
        self.posted: list[tuple] = []

    def post(self, board, title, content):
        self.posted.append((board, title, content))
        return {"id": 1}


def test_post_day_composes_from_episodes_and_posts():
    eps = EpisodicMemory()
    eps.record(Episode(tick=1, kind="skill", summary="forge dagger", reward=0.6))
    agent = SimpleNamespace(persona=Persona(name="Tormund", title="a blacksmith"), episodes=eps)
    client = _FakeClient()
    res = post_day(agent, job="blacksmith", board="tavern", client=client)
    assert res == {"id": 1}
    assert len(client.posted) == 1 and client.posted[0][0] == "tavern"


def test_post_day_noops_without_api_key():
    eps = EpisodicMemory()
    agent = SimpleNamespace(persona=Persona(name="X"), episodes=eps)
    assert post_day(agent, client=ForumClient(api_key="")) is None
