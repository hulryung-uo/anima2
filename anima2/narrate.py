"""What the agent is doing, and WHY — in words, for a human watching the character.

Every readout this project had before this one is TELEMETRY: `trip=`, `landed=`, `want=`,
`retired=`. They are dense, they are for grepping a tape afterwards, and they assume a
reader who already knows the machine. None of them answers the question someone watching
the character through `--monitor` actually asks, which is *"why is he doing that?"*.

That question is worth answering because the answer is CHECKABLE BY A HUMAN. A person
watching a miner walk past ore they can see, or sell at a vendor that pays badly, knows
something is wrong long before any counter does — but only if the agent says what it
believed at the time. This module is the input side of that loop; `docs/OBSERVATIONS.md`
is where what it reveals gets written down.

NOTHING NEW IS RECORDED to produce any of it. Same discipline as `market.walk_readout` and
`life_runner.retirement_reason`: every clause below is a projection of state that already
exists for a non-narration reason — `mode`, `target_cap`, the admitted capability frame,
`mkt_phase`, `smelt_phase`, the harvest stuck window, the walk's own cursor. A narration
layer that needed its own bookkeeping would drift from the behaviour it describes, and a
narrator that lies is worse than one that says nothing.

TWO REGISTERS, deliberately:

* `short` goes in-game via `Say`, so it lands in the journal and is visible in the same
  client window the character is being watched in. It is one clause, because it is read at
  a glance while something is moving.
* `detail` goes to the terminal, where there is room for the EVIDENCE — the threshold that
  fired, the tile being walked to, the distance still to cover, the age of the frame. That
  is what turns "he is going to the vendor" into a claim a watcher can disagree with.

THE COST IS ZERO, and that is a design constraint rather than a nice property.
`WarriorLife.tick` ticks exactly ONE inner agent per orchestrator tick and says in its own
comment that this is load-bearing: two would split `new_journal`, which is a
since-last-observe delta. A narrator that spent a tick on speech would skip an agent tick
and quietly distort every per-tick instrument that reads that journal — including the
mining cause split, which counts ONE sample per tick and would silently merge two swings'
verdicts into one. So `Say` is only ever emitted on a tick where the agent itself chose to
do nothing (see `village._run_worker`), and the line waits in a queue until such a tick
arrives. Intent changes on phase boundaries, which are tens of ticks apart, so the wait
costs nothing legible.
"""

from __future__ import annotations



_UNKNOWN = ("thinking", "no readable intent — the narrator could not read this agent",
            "unknown")


def _econ(agent):
    """A Life's economy agent, or the agent itself — the same resolution
    `life_runner.frame_retirements` makes, and for the same reason."""
    return getattr(agent, "econ_agent", None) or agent


def _mine_intent(memory: dict, pos) -> tuple[str, str] | None:
    """The miner's own loop: dig, smelt, carry, come back. `None` if this agent is not
    running one, so the caller can fall through to the economy reading."""
    phase = memory.get("smelt_phase")
    if phase is None:
        return None
    stuck = memory.get("harvest_recent_stuck")
    win = f"{sum(1 for s in stuck if s)}/{len(stuck)}" if stuck else "0/0"
    if memory.get("harvest_relocating"):
        dest = memory.get("harvest_relocate_to")
        where = f" toward {tuple(dest)}" if dest else ""
        return ("this rock is dead — moving to a new face",
                f"relocating{where}: {win} of the recent swing verdicts were failures, "
                f"so this stand is judged worked out", f"mine:relocate:{dest}")
    if phase == "smelt":
        return ("smelting what I dug",
                "ore in pack — smelting to ingots before carrying, because ingots are what "
                "the smith takes", "mine:smelt")
    if phase == "deliver":
        drop = memory.get("smithy_drop")
        return ("carrying ingots to the smith",
                f"delivering to {tuple(drop) if drop else '?'} — the tinker cannot craft "
                f"until this lands on the ground where he can reach it", "mine:deliver")
    if phase == "return":
        home = memory.get("miner_home")
        d = ("?" if not home or pos is None
             else max(abs(pos.x - home[0]), abs(pos.y - home[1])))
        return ("heading back to my rock",
                f"walking home to {tuple(home) if home else '?'}, {d} tiles out — mining "
                f"resumes when I arrive", "mine:return")
    return ("mining", f"swinging at the face; recent failures {win}", "mine:dig")


def _economy_intent(agent, obs) -> tuple[str, str] | None:
    """What transaction this agent is in the middle of, and what made it want one."""
    econ = _econ(agent)
    memory = dict(getattr(econ, "memory", None) or {})
    frame = getattr(getattr(econ, "goal_stack", None), "current", None)
    cap = None
    age = ""
    if frame is not None:
        cap = frame.goal.params.get("capability")
        if frame.deadline_tick is not None:
            age = (f", {econ.ticks - frame.created_tick} of "
                   f"{frame.deadline_tick - frame.created_tick} ticks used")
    want = getattr(agent, "target_cap", None)
    if cap is None and want is None:
        return None

    from .skills.market import walk_readout

    walk = walk_readout(memory, obs.player.pos) if obs is not None else "trip=?"
    phase = memory.get("mkt_phase")
    doing = cap or want
    # The walk half, when there is one — this is the part a watcher can check against what
    # they can see on screen, so it carries the tile and the distance rather than a phase.
    leg = ""
    if "to=" in walk:
        tile = walk.split("to=")[1].split()[0]
        gap = walk.split("d=")[1].split()[0] if "d=" in walk else "?"
        leg = f", walking to {tile} (distance {gap})"
    if cap is None:
        return (f"I want to {want} but nothing has admitted it yet",
                f"want={want}, no frame on the stack — a readiness gate is refusing, which "
                f"is the state worth watching for", f"econ:want:{want}")
    # The KEY deliberately excludes the frame's age and the distance: those change every
    # tick, and keying on the rendering is how a narrator becomes a per-tick spammer. It
    # is the phase and the destination that constitute a change of intent.
    return (f"{str(doing).replace('_', ' ')}",
            f"admitted {doing}{age}{leg}; market phase {phase or 'none'}",
            f"econ:{doing}:{phase}:{leg.split('(')[0]}")


def intent(agent, obs) -> tuple[str, str, str]:
    """`(short, detail, key)` — a clause for the game journal, a line for the terminal, and
    the CHANGE KEY the caller throttles on.

    The key exists because the first version of this had none and narrated every single
    tick: the detail carries the frame's age and the distance still to walk, both of which
    move continuously, so "has the rendering changed" is not "has the intent changed". That
    is the same defect this file's own alarms have been burned by twice — `FRAME OVERDUE`
    measured 3,881 identical lines in one 4,000-tick run before it was throttled. The key
    is the phase and the destination, and nothing that ticks.

    Never raises and never returns empty strings: a narrator that goes quiet is
    indistinguishable from an agent with nothing to say, and the whole point is that a
    watcher can trust the silence to mean something. See the module docstring.
    """
    try:
        pos = obs.player.pos if obs is not None else None
        mode = getattr(agent, "mode", None)
        if mode == "hunt" or (mode is None and getattr(agent, "econ_agent", None)):
            hp = f"{obs.player.hits}/{obs.player.hits_max}" if obs is not None else "?"
            if mode == "hunt":
                return ("looking for work and watching my back",
                        f"hunt mode at hp {hp} — no transaction is owed, so the economy "
                        f"agent is idle by design", "hunt")
        # A miner's own loop first: it is the whole job for that agent, and it has no
        # capability frames at all (`landed=0/0` all day, which is correct and confusing).
        mine = _mine_intent(dict(getattr(agent, "memory", None) or {}), pos)
        if mine is not None:
            return mine
        econ = _economy_intent(agent, obs)
        if econ is not None:
            return econ
        skill = getattr(agent, "last_skill_name", None)
        if skill:
            return (f"{str(skill).replace('_', ' ')}", f"running skill {skill}",
                    f"skill:{skill}")
        return _UNKNOWN
    except Exception:  # noqa: BLE001 — narration must never break the run
        return _UNKNOWN
