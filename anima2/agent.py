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
from .skills.base import Goal, SkillContext, Status


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
    ) -> None:
        self.body = body
        self.persona = persona
        self.planner = planner
        self.reflexes = reflexes or Reflexes()
        self.cognition = cognition or NullCognition()
        self.goal = goal
        self.cognition_interval = cognition_interval
        self.memory: dict = {}
        self.episodes = EpisodicMemory()
        self.ticks = 0

    def tick(self) -> Action | None:
        """Run one fast-loop iteration. Returns the action taken (or `None`)."""
        obs = self.body.observe()
        ctx = SkillContext(
            obs=obs,
            persona=self.persona,
            goal=self.goal,
            memory=self.memory,
            episodes=self.episodes.recent(8),
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
