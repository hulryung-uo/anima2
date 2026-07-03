"""Movement skills: wander aimlessly, or walk toward a target tile."""

from __future__ import annotations

from ..contract import Position, Walk, WalkTo
from ..geometry import chebyshev, direction_toward
from .base import Skill, SkillContext, SkillResult, Status


class Wander(Skill):
    """Step in a steady direction, turning when blocked. The default 'be alive' skill."""

    name = "wander"
    description = "Walk around aimlessly, changing direction when movement stalls."

    def step(self, ctx: SkillContext) -> SkillResult:
        d = ctx.memory.get("wander_dir", 2)  # default East
        last = ctx.memory.get("wander_last_pos")
        cur = (ctx.obs.player.pos.x, ctx.obs.player.pos.y)
        # In UO the first walk in a NEW direction only turns you (no move), so a
        # single no-move tick isn't "blocked". Give each direction a real step
        # (turn + move) before rotating — otherwise we'd spin in place forever.
        stuck = ctx.memory.get("wander_stuck", 0) + 1 if last == cur else 0
        if stuck >= 2:
            d = (d + 1) % 8
            stuck = 0
        ctx.memory["wander_dir"] = d
        ctx.memory["wander_stuck"] = stuck
        ctx.memory["wander_last_pos"] = cur
        return SkillResult(Status.RUNNING, Walk(dir=d, run=False))


class GoTo(Skill):
    """Walk toward ``goal.params['target']`` (a Position). SUCCESS on arrival.

    Delegates to the body's A* route driver (`WalkTo`, `anima-net`'s
    `Session::advance_route` — routes around static obstacles like mountains
    and buildings) rather than stepping greedily. `WalkTo` is fire-and-forget
    on the wire — there is no "arrived"/"blocked" reply — so this skill emits
    it **once** per attempt and then *monitors*, purely from position deltas
    across successive Observations (the only progress signal that exists;
    confirmed by reading `anima-net`'s `lib.rs`/`json.rs` — no route state is
    exposed in the observation JSON), exactly like the ground truth's
    "position deltas at minimum" framing:

    - **Progress** means the player's tile position *changed at all* since
      the last tick — **not** "distance to `target` improved". A real A*
      route around an obstacle routinely moves *away* from the target for a
      while before curving back (live-confirmed: a calibrated course a few
      dozen tiles apart, greedy-blocked by a Minoc-ridge spur, had `WalkTo`'s
      own distance-to-target climb for ~50 ticks — further than the start —
      before it started closing again; see `live_navigate.py`). A
      distance-must-improve watermark would misread that healthy detour as a
      stall and abandon a working route. Any movement — toward, away,
      sideways — resets the stall counter; no new action is sent while it
      does, since the route already in flight keeps advancing on its own,
      driven by the body's own `pump` cadence (`IpcBody.observe()` pumps
      every tick).
    - **A genuine stall** (`walkto_stall_limit` ticks with the tile position
      *completely unchanged*) is bounded-retried: re-issue `WalkTo` once more
      (up to `walkto_max_retries` times) — a fresh route starts with an empty
      deny-blacklist, so a route that gave up because it painted itself into
      a corner (`RouteStep::Done` with no path left, silently dropped
      Rust-side — see `lib.rs`) sometimes succeeds on a clean retry.
    - **No progress at all** even after every retry (the exact "no terrain
      data loaded" / "degenerate route" / mock-body-ignores-WalkTo case the
      ground truth calls out) falls back to the **old greedy stepping**
      (`Walk` toward `target`, `direction_toward`) so no existing behaviour
      regresses — this is what makes `GoTo` still work under `MockBody`
      (which has no route driver at all: `WalkTo` is silently accepted as a
      no-op, exactly like "no progress"). The active mode is written to
      ``ctx.memory['goto_mode']`` ("walkto" or "greedy") so tests (and any
      other caller) can observe the fallback happening — deliberately *not*
      wiped on a terminal SUCCESS/FAILURE (unlike every other key this skill
      owns), so it survives as a "how did the last attempt finish" breadcrumb
      even after the goal that drove it is gone.
    - Greedy fallback itself is still stall-bounded (`stall_limit`, unchanged
      from the pre-A* version) — sustained no progress there is a genuine
      dead end (or a target no path exists to at all) → FAILURE, same
      "wedged, let a higher layer re-plan" contract as before.

    Arrival is an **exact** tile match (chebyshev distance 0), unchanged from
    the pre-A* version — `WalkTo`'s own Rust-side route target is likewise an
    exact tile, so this needs no change for the new mode. (A live proof that
    wants a looser "close enough" gate, e.g. because the exact destination
    tile is awkward to stand on, applies that leniency at the *call site*,
    not here — see `live_navigate.py`.)
    """

    name = "goto"
    description = (
        "Walk toward a target tile via the body's A* route (WalkTo), monitoring "
        "progress from position deltas and falling back to greedy stepping if "
        "the route makes no progress at all."
    )
    consumes_goal = True  # arriving (or wedging) uses up the goto goal

    def can_run(self, ctx: SkillContext) -> bool:
        return ctx.goal is not None and ctx.goal.kind == "goto"

    #: Escape hatch: setting `use_walkto = False` on an instance skips the
    #: WalkTo probe entirely and goes straight to (and stays on) greedy
    #: stepping — byte-for-byte the pre-A* skill. Not needed for normal use
    #: (the default already falls back to greedy on its own whenever WalkTo
    #: isn't paying off); exists so a **control** run can exercise "pure
    #: greedy" through the real shipped skill rather than a hand-rolled
    #: reimplementation — see the greedy vs. WalkTo differential proof in
    #: `live_navigate.py`.
    use_walkto: bool = True

    #: Consecutive no-progress ticks in *greedy* mode before declaring
    #: ourselves wedged. The first step in a new direction is a UO *turn* (no
    #: move), so this must be >1. Unchanged from the pre-A* version.
    stall_limit: int = 4

    #: Consecutive completely-unmoved ticks in *walkto* mode before treating
    #: the route as stalled (mirrors `stall_limit`'s role, one layer up —
    #: "unmoved", not "no closer to target": see the class docstring). Sized
    #: against the fastest tick cadence in use (village/fleet pump at 300ms):
    #: the route driver recovers from a single server deny in ~1.2s of wall
    #: time, which is 4 ticks at that cadence — 6 keeps one deny cycle from
    #: reading as a stall and burning a retry.
    walkto_stall_limit: int = 6
    #: How many times a stalled WalkTo route is re-issued (fresh blacklist)
    #: before giving up on A* entirely and falling back to greedy stepping.
    #: Live-calibrated, not a guess: on the differential proof's own
    #: round-trip course (`live_navigate.py`), a route occasionally hit a
    #: transient multi-tick stall mid-route (not the immediate, permanent
    #: "never moves at all" case a missing-map-data/`MockBody` body produces)
    #: that a single retry didn't clear but a further one did — re-probing the
    #: *exact same* stuck tile by hand (fresh `WalkTo`, no accumulated
    #: deny-blacklist) confirmed it reliably resolves within a tick or two, so
    #: this is "genuinely worth a few clean-slate attempts", not "immediately
    #: hopeless" — 1 retry cut a real route short before it recovered.
    walkto_max_retries: int = 3

    #: `ctx.memory` keys this skill owns. All of them are wiped whenever the
    #: goal's target changes (`_reset`, a fresh attempt). Only the *transient*
    #: ones (everything but `goto_mode` — see its own comment above) are wiped
    #: on a terminal SUCCESS/FAILURE (`_clear`), so a finished/abandoned
    #: attempt never leaks stall/retry bookkeeping into the next one, while
    #: `goto_mode` itself stays readable as a breadcrumb.
    _RESET_KEYS = (
        "goto_target", "goto_mode", "goto_walkto_last_pos", "goto_walkto_stall",
        "goto_walkto_retries", "goto_stall", "goto_last_pos",
    )
    _TERMINAL_KEYS = tuple(k for k in _RESET_KEYS if k != "goto_mode")

    def step(self, ctx: SkillContext) -> SkillResult:
        assert ctx.goal is not None
        target: Position = ctx.goal.params["target"]
        here = ctx.obs.player.pos
        target_key = (target.x, target.y)

        # A new (or changed) target starts a fresh attempt: probe with WalkTo
        # again from scratch rather than carrying over another target's stall
        # bookkeeping (or a mode it fell back to for a since-abandoned goal).
        if ctx.memory.get("goto_target") != target_key:
            self._reset(ctx, target_key)

        if chebyshev(here, target) == 0:
            self._clear(ctx)
            return SkillResult(Status.SUCCESS, None, reward=1.0)

        default_mode = "walkto" if self.use_walkto else "greedy"
        if ctx.memory.get("goto_mode", default_mode) == "walkto":
            result = self._walkto_step(ctx, here, target)
            if result is not None:
                return result
            # Fell through: every retry stalled with zero improvement — hand
            # off to greedy stepping, starting fresh, same tick (no ticks
            # wasted just recording the switch).
            ctx.memory["goto_mode"] = "greedy"
            ctx.memory["goto_stall"] = 0
            ctx.memory.pop("goto_last_pos", None)

        return self._greedy_step(ctx, here, target)

    def _reset(self, ctx: SkillContext, target_key: tuple[int, int]) -> None:
        for key in self._RESET_KEYS:
            ctx.memory.pop(key, None)
        ctx.memory["goto_target"] = target_key
        ctx.memory["goto_mode"] = "walkto" if self.use_walkto else "greedy"

    def _clear(self, ctx: SkillContext) -> None:
        for key in self._TERMINAL_KEYS:
            ctx.memory.pop(key, None)

    def _walkto_step(self, ctx: SkillContext, here: Position, target: Position) -> SkillResult | None:
        """One tick of `WalkTo`-delegated progress monitoring. Returns a
        `SkillResult` to act on (issuing `WalkTo`, or `RUNNING` with no action
        while the route advances on its own), or `None` to signal "give up on
        A*, fall back to greedy" — `walkto_max_retries` exhausted with the
        tile position never once changing.
        """
        cur = (here.x, here.y)
        last = ctx.memory.get("goto_walkto_last_pos")
        if last is None or cur != last:
            # Moved since the last tick (including the very first tick of
            # this attempt, when there's no `last` yet) — reset the stall
            # counter. No action: the route already in flight keeps
            # advancing on its own each time the body is pumped
            # (`IpcBody.observe()`, every tick). Deliberately *not*
            # "distance to target improved" — see the class docstring.
            first = last is None
            ctx.memory["goto_walkto_last_pos"] = cur
            ctx.memory["goto_walkto_stall"] = 0
            # A recovered stall shouldn't count against future ones: the
            # retry budget guards a route that never comes back, not a long
            # route with several separated, individually-recovered hiccups.
            ctx.memory.pop("goto_walkto_retries", None)
            if first:
                return SkillResult(Status.RUNNING, WalkTo(x=target.x, y=target.y))
            return SkillResult(Status.RUNNING, None)

        stall = ctx.memory.get("goto_walkto_stall", 0) + 1
        ctx.memory["goto_walkto_stall"] = stall
        if stall < self.walkto_stall_limit:
            return SkillResult(Status.RUNNING, None)

        retries = ctx.memory.get("goto_walkto_retries", 0)
        if retries >= self.walkto_max_retries:
            return None  # give up on A* — caller falls back to greedy

        # Bounded retry: a fresh route starts with an empty deny-blacklist,
        # so re-issuing can succeed even when the stalled attempt couldn't.
        ctx.memory["goto_walkto_retries"] = retries + 1
        ctx.memory["goto_walkto_last_pos"] = cur
        ctx.memory["goto_walkto_stall"] = 0
        return SkillResult(Status.RUNNING, WalkTo(x=target.x, y=target.y))

    def _greedy_step(self, ctx: SkillContext, here: Position, target: Position) -> SkillResult:
        """Step-by-step `Walk` toward `target` (no routing around obstacles) —
        the pre-A* behaviour, preserved verbatim as the fallback for a body
        with no route driver (`MockBody`) or a route making no progress at
        all. `stall_limit`-bounded, same "wedged → FAILURE" contract as before.
        """
        cur = (here.x, here.y)
        stall = ctx.memory.get("goto_stall", 0) + 1 if ctx.memory.get("goto_last_pos") == cur else 0
        ctx.memory["goto_stall"] = stall
        ctx.memory["goto_last_pos"] = cur

        if stall >= self.stall_limit:
            self._clear(ctx)
            return SkillResult(Status.FAILURE, None)

        return SkillResult(Status.RUNNING, Walk(dir=direction_toward(here, target), run=False))
