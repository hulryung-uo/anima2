"""Distance-based movement evidence, independent of a walk FSM's retry counters."""

from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class _Approach:
    best: int
    since: int
    seen: int
    attempts: int = 0
    reported: int = 0


class ApproachWatch:
    """Remember failed approaches across trip retries, even when the player moves.

    A lower best distance or real output resets the window. A new transaction id,
    walking sideways, or abandoning and retrying the same shop does not. A majority
    of the window must contain walking attempts, so occasional walks by a stationary
    worker do not turn ordinary work into a wedge alarm.
    """

    def __init__(self, threshold: int = 240, capacity: int = 16) -> None:
        if threshold < 1 or capacity < 1:
            raise ValueError("watch threshold and capacity must be positive")
        self.threshold = threshold
        self.capacity = capacity
        self._routes: OrderedDict[tuple, _Approach] = OrderedDict()
        self._output = None

    def observe(self, destination, pos, tick: int, *, attempted: bool, output):
        if output != self._output:
            self._routes.clear()
            self._output = output
        if destination is None:
            return None
        key = (destination.phase, destination.x, destination.y, destination.reach)
        distance = destination.distance(pos)
        if distance <= destination.reach:
            self._routes.pop(key, None)
            return None
        state = self._routes.get(key)
        if (state is None or distance < state.best
                or tick - state.seen > self.threshold):
            state = self._routes[key] = _Approach(distance, tick, tick)
        else:
            state.attempts += attempted
            state.seen = tick
        self._routes.move_to_end(key)
        while len(self._routes) > self.capacity:
            self._routes.popitem(last=False)
        elapsed = tick - state.since
        if (elapsed >= self.threshold and state.attempts * 2 >= elapsed
                and tick - max(state.since, state.reported) >= self.threshold):
            state.reported = tick
            return elapsed, state.attempts, distance, state.best
        return None
