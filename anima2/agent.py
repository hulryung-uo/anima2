"""The Agent — the two-rate control loop that makes a persona *live*.

- **Fast loop** (`tick`): perceive → reflexes → planner → run a skill → act. Pure,
  deterministic, no LLM. The agent is always alive here.
- **Slow loop** (`Cognition`, async/occasional): sets the high-level `Goal` the
  planner serves, handles social/novelty, reflects. Phase 1 ships a stub; the real
  LLM cognition drops in behind this interface without touching the fast loop
  (DESIGN.md §3.3).
"""

from __future__ import annotations

from typing import Protocol

from .body import Body
from .contract import Action
from .memory import Episode, EpisodicMemory
from .persona import Persona
from .planner import Planner
from .reflexes import Reflexes
from .skill_library import SkillLibrary
from .skills.base import Goal, SkillContext, Status


_BACKPACK_LAYER = 0x15


class Cognition(Protocol):
    """The slow, goal-setting layer (LLM in production)."""

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        """Return an updated goal given the current situation (may be `None`)."""
        ...


class NullCognition:
    """Phase-1 stub: never changes the goal. Replace with an LLM-backed cognition."""

    def reconsider(self, ctx: SkillContext) -> Goal | None:
        return ctx.goal


class Agent:
    def __init__(
        self,
        body: Body,
        persona: Persona,
        planner: Planner,
        reflexes: Reflexes | None = None,
        cognition: Cognition | None = None,
        *,
        goal: Goal | None = None,
        cognition_interval: int = 20,
        episodes_window: int = 20,
        skill_library: SkillLibrary | None = None,
        profession: str = "",
    ) -> None:
        self.body = body
        self.persona = persona
        self.planner = planner
        self.reflexes = reflexes or Reflexes()
        self.cognition = cognition or NullCognition()
        self.goal = goal
        self.cognition_interval = cognition_interval
        #: Optional collaborator (PHASE4.md item 3, `skill_library.py`) — `None`
        #: (the default) makes `tick()`'s outcome-ledger call below a byte-for-
        #: byte no-op, so every existing caller is unaffected (see
        #: `test_agent_skill_library_none_is_byte_for_byte_noop`).
        self.skill_library = skill_library
        #: The profession key (`profession.py::Profession.key`, e.g. "hunter",
        #: "miner") this agent is playing — `Agent` itself has no other notion
        #: of "job" today (`Persona` carries identity/voice, not a profession
        #: string). Only consulted by the `skill_library` ledger call below, as
        #: half of its `(skill_name, profession)` key — the same pairing
        #: `SkillLibrary.stats()` looks up by. Defaults to `""` (an explicit,
        #: harmless "unset" key) so passing `skill_library=` alone still works.
        self.profession = profession
        #: How many recent episodes `SkillContext.episodes` carries each tick. This
        #: is the ceiling every cognition layer sees — notably `ReflectingCognition`,
        #: whose own `episode_window` can never look further back than this (it
        #: slices `ctx.episodes`, so a bigger `episode_window` than `episodes_window`
        #: is silently capped). Defaults to `ReflectingCognition`'s default
        #: (`episode_window=20`) so the common `ThreadedCognition(ReflectingCognition(
        #: inner))` composition gets its full window out of the box; bump both
        #: together if you want reflection to see further back.
        self.episodes_window = episodes_window
        self.memory: dict = {}
        self.episodes = EpisodicMemory()
        self.ticks = 0

    def tick(self) -> Action | None:
        """Run one fast-loop iteration. Returns the action taken (or `None`)."""
        obs = self.body.observe()
        # Keep a rolling *living* snapshot even during a corpse run. A second
        # death is a new recovery episode and must use the state immediately
        # before that death, not evidence frozen for the previous corpse.
        if not obs.player.dead and obs.player.body:
            backpacks = {
                item.serial
                for item in obs.items
                if item.container == obs.player.serial and item.layer == _BACKPACK_LAYER
            }
            equipped = {
                item.serial
                for item in obs.items
                if item.container == obs.player.serial
                and item.layer > 0
                and item.layer != _BACKPACK_LAYER
            }

            # Include nested backpack contents as ownership evidence. ItemView
            # does not expose "is container", so treating every discovered item
            # as a possible parent is the safe generic traversal: only actual
            # parent serials can pull another item into the set.
            pack_owned: set[int] = set()
            frontier = set(backpacks)
            while frontier:
                discovered = {
                    item.serial
                    for item in obs.items
                    if item.container in frontier and item.serial not in pack_owned
                }
                pack_owned.update(discovered)
                frontier = discovered

            self.memory["death_rolling_alive_body"] = obs.player.body
            self.memory["death_rolling_alive_pos"] = (
                obs.player.pos.x,
                obs.player.pos.y,
                obs.player.pos.z,
            )
            self.memory["death_rolling_equipped"] = equipped
            self.memory["death_rolling_pack_owned"] = pack_owned

        was_dead = bool(self.memory.get("death_observed_dead", False))
        if obs.player.dead and not was_dead:
            # Freeze attribution evidence exactly once on the alive->dead edge.
            # Clearing first prevents a dead-on-login/new-episode observation
            # without usable rolling evidence from inheriting an older corpse.
            for key in (
                "death_last_alive_body",
                "death_last_alive_pos",
                "death_last_equipped",
                "death_last_pack_owned",
            ):
                self.memory.pop(key, None)
            body = int(self.memory.get("death_rolling_alive_body", 0))
            pos = self.memory.get("death_rolling_alive_pos")
            if body > 0 and isinstance(pos, tuple) and len(pos) == 3:
                self.memory["death_last_alive_body"] = body
                self.memory["death_last_alive_pos"] = pos
                self.memory["death_last_equipped"] = set(
                    self.memory.get("death_rolling_equipped", set())
                )
                self.memory["death_last_pack_owned"] = set(
                    self.memory.get("death_rolling_pack_owned", set())
                )
            self.memory["death_episode"] = int(self.memory.get("death_episode", 0)) + 1
        self.memory["death_observed_dead"] = obs.player.dead
        ctx = SkillContext(
            obs=obs,
            persona=self.persona,
            goal=self.goal,
            memory=self.memory,
            episodes=self.episodes.recent(self.episodes_window),
            episode_count=self.episodes.total_recorded,
        )

        # Slow loop, sampled: let cognition re-set the goal occasionally.
        if self.ticks % self.cognition_interval == 0:
            self.goal = self.cognition.reconsider(ctx)
            ctx.goal = self.goal

        self.ticks += 1

        # 1) Reflexes pre-empt everything.
        action = self.reflexes.check(obs, self.persona)
        if action is not None:
            self.body.act(action)
            return action

        # 2) Planner picks a skill; the skill produces an action.
        skill = self.planner.select(ctx)
        result = skill.step(ctx)

        # Record terminal/rewarded outcomes to episodic memory (the learning signal
        # + cognition context). RUNNING-with-no-reward steps are too noisy to log.
        if result.reward or result.status is not Status.RUNNING:
            self.episodes.record(
                Episode(
                    tick=self.ticks,
                    kind="skill",
                    summary=f"{skill.name} → {result.status.name.lower()}",
                    reward=result.reward,
                    pos=(obs.player.pos.x, obs.player.pos.y),
                )
            )
            # PHASE4.md item 3: the exact same filter as the episodic record
            # above (not a separate, looser one) — a skill_library=None agent
            # never reaches this branch at all, and one wired in only ever
            # ledgers what episodic memory itself would have kept.
            if self.skill_library is not None:
                self.skill_library.record_outcome(skill.name, self.profession, result.reward, result.status)

        # A goal-serving skill (e.g. GoTo) that reached a terminal state — arrived
        # (SUCCESS) or got wedged (FAILURE) — has consumed the goal: clear it so the
        # agent resumes its default behaviour and cognition picks the next goal.
        # (Only such skills clear it — a high-priority SpeakPending/Greet returning
        # SUCCESS must NOT drop an active goto; and leaving a FAILED goto in place
        # would make GoTo retry into the same wall every tick.)
        if result.status is not Status.RUNNING and self.goal is not None and skill.consumes_goal:
            self.goal = None
        if result.action is not None:
            self.body.act(result.action)
        return result.action

    def run(self, ticks: int) -> None:
        """Run the fast loop for a fixed number of ticks (synchronous demo driver)."""
        for _ in range(ticks):
            if not self.body.connected:
                break
            self.tick()
