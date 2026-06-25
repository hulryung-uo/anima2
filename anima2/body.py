"""The Body interface — anima2's view of whatever drives the UO connection.

The brain only ever talks to a `Body`. In production this is backed by
``anima-core``/``anima-net`` (Rust) over an IPC bridge; in tests and offline
development it's `MockBody`. This is the concrete embodiment of Brain ⊥ Body
(DESIGN.md A2): the brain reads `Observation`s and emits `Action`s, nothing else.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contract import Action, Observation


@runtime_checkable
class Body(Protocol):
    """A drivable UO character."""

    def observe(self) -> Observation:
        """Return the latest perception snapshot."""
        ...

    def act(self, action: Action) -> None:
        """Execute one high-level intent."""
        ...

    @property
    def connected(self) -> bool:
        """Whether the body is still attached to a live world."""
        ...
