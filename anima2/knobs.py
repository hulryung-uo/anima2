"""The ONE clamped read every tuning knob goes through (audit proposal 5, generalized).

**The scope used to be narrower than the name, and this paragraph used to say so.** Until
2026-08-05 it read: *"'Life' is the honest scope, not a hedge. `wander_leash` is a tunable
threshold too, and it does NOT come through here ... a malformed value falls back to the
CLASS DEFAULT rather than to a floor, which is a different rule from `_clamped`'s ...
claiming 'every knob' while that one rides a second channel with a second clamp is how a
single-source module quietly stops being one."* Audit follow-up 4 closed it:
`skills/movement.py::wander_leash` is that key's single read point and it delegates here,
so the carve-out is gone and the word "every" is now load-bearing rather than aspirational.

Which direction the unification went is the interesting part, and it is recorded in that
function: toward the FLOOR, not toward the class-default fallback. The fallback makes the
mapping discontinuous (2 -> 2, 1 -> 1, -1 -> 8), so a searcher stepping one below the floor
leaps to the shipped default instead of resting on the boundary. Floor-clamping is the
monotone shape every other knob already had, and a monotone boundary is what makes an axis
searchable at all.

A knob is a threshold a genome axis / bandit / slow-loop steering write may set. The
lesson this module exists to make structural was paid for twice, and both payments are
written down: `skills/market.py::_bank_reserve` and the constructor comment in
`warrior_life.py`.

**One key was not enough.** `bank_reserve` started as a single memory key shared by the
Life's decide rule, the `bank_gold` readiness gate and `BankGold`'s own FSM — the whole
point being that one number decides when to bank, what admission allows, and how much
the skill leaves behind. That was still not enough: a MALFORMED value (a negative from a
genome axis exploring, a float, a stray non-int from a bad steering write) read RAW by
the rule while the gate read it through a clamp made the rule want `bank_gold` at any
gold while the gate refused at 0g — the rule-vs-gate drift class, recreated THROUGH the
tuning knob that was supposed to be safe (review-caught when the tinker's urgent-bank
branch hoisted a raw read above craft). One key AND one read point, or it is not a knob,
it is a new drift avenue.

So: every knob reader — rule side, gate side, FSM side — calls in here, and a
present-but-malformed value collapses to the same floor on all of them. Agreement then
holds by construction rather than by everyone remembering to clamp.

Deliberately the lowest layer in the package: this module imports nothing from `anima2`.
A clamp that could import a skill, a capability or a Life would eventually be tempted to
special-case one of them, and the moment it does, the "one read point" property is gone.

Two absence conventions, because knobs arrive two ways (both live shapes):
- `knob_int(memory, key, default)` — the knob rides a MEMORY KEY, because a `decide`
  rule is a `staticmethod` over `(obs, memory)` and can see nothing else. This is the
  `bank_reserve` shape, and it is mandatory for any knob a rule reads.
- `knob_param(value, default)` — the knob rides an INSTANCE ATTRIBUTE, for a threshold
  only `tick()` or a method reads. This is the `econ_grace` shape. `None` means
  "not tuned"; anything else goes through the identical clamp.
Getting those backwards is how you reach a rule that cannot see its own knob.
"""

from __future__ import annotations

from typing import Any, Mapping


def _clamped(value: Any, floor: int) -> int:
    """The clamp itself — the single definition both public readers share.

    A value that is not exactly an `int` (a float, a `bool`, a string, `None`) or that
    sits below `floor` becomes `floor`. `type(value) is int` rather than `isinstance`
    on purpose: `bool` IS an `int` subclass, and `True` reaching a threshold as `1` is
    a malformed write being silently honoured rather than clamped.
    """
    return value if type(value) is int and value > floor else floor


def knob_int(memory: Mapping[str, Any], key: str, default: int = 0, *,
             floor: int = 0) -> int:
    """Read tuning knob `key` out of `memory`, clamped to at least `floor`.

    `default` applies ONLY when the key is ABSENT — every caller of a given key must
    agree on the value, but they need not agree on what "unset" means. That asymmetry
    is load-bearing: a Life passes its own derived constant as the default while the
    gate and the FSM pass 0 (unset == no reserve, byte-identical to the behavior that
    predates knobs), and forward safety still holds because the Life's default is the
    stricter of the two.

    A PRESENT-but-malformed value clamps on every reader, which is the property the
    module docstring exists for: a rule reading raw while a gate reads clamped is the
    drift class, reachable through the knob itself.
    """
    if key not in memory:
        return default
    return _clamped(memory.get(key, 0), floor)


def knob_param(value: Any, default: int, *, floor: int = 0) -> int:
    """The same clamp for a knob that arrives as a CONSTRUCTOR PARAMETER.

    `None` means "not tuned" — the caller's own default stands, unclamped, because a
    module constant is trusted code and not a tuning write. Anything else goes through
    `_clamped`, so an exploring axis cannot hand a Life a negative hysteresis window
    any more than it can hand a rule a negative reserve.

    `floor` matters more here than it does for a reserve: 0 is a safe floor for "how
    much gold to keep" but a catastrophic one for "how many ticks before the
    disagreement detector fires" — at 0 the detector would fire every tick and its
    repair would close a surface on every one of them. Each caller states its own.
    """
    return default if value is None else _clamped(value, floor)
