"""Harvesting skills — gather a resource by using a tool on the surrounding tiles.

The UO harvest loop is identical for mining, lumberjacking, and fishing: **use the
tool → the server opens a target cursor → target a resource tile → harvest.** The
success signal is the relevant skill's base rising (0x3A) — unambiguous and
already parsed (dig/chop results are cliloc 0xC1, available but not needed). So
`Harvest` rewards on skill gain and probes the 8 surrounding tiles round-robin to
keep hitting resource nodes.
"""

from __future__ import annotations

from collections import deque

from ..contract import Equip, PickUp, Position, TargetGround, Use, WalkTo
from ..geometry import chebyshev
from .base import Skill, SkillContext, SkillResult, Status

BACKPACK_LAYER = 0x15

def _ring(max_r: int) -> list[tuple[int, int]]:
    """All tiles within Chebyshev `max_r` of the player, nearest ring first."""
    return [
        (dx, dy)
        for r in range(1, max_r + 1)
        for dx in range(-r, r + 1)
        for dy in range(-r, r + 1)
        if max(abs(dx), abs(dy)) == r
    ]


# Probed round-robin to find a resource near where we stand. Mining/lumberjacking
# reach 2; fishing casts up to 4 tiles.
PROBE_OFFSETS = _ring(2)
FISH_OFFSETS = _ring(4)

# Tool item graphics (ServUO art ids).
PICKAXE_GRAPHICS = frozenset({0x0E85, 0x0E86, 0x0F39, 0x0F3A})  # pickaxe / shovel
AXE_GRAPHICS = frozenset({0x0F43, 0x0F44, 0x0F47, 0x0F49, 0x0F4B, 0x13B0, 0x13FB, 0x1443})
POLE_GRAPHICS = frozenset({0x0DBF, 0x0DC0})  # fishing pole

# UO skill ids.
SKILL_LUMBERJACKING = 44
SKILL_MINING = 45
SKILL_FISHING = 18

# Cliloc 500493 "There's not enough wood here to harvest." — the node is tapped out.
NODE_DEPLETED_CLILOC = 500493
# Cliloc 1008124 "You pull out an item :" — a successful catch (fishing's output is
# fish, not skill, which gains very slowly — so reward catches directly).
CATCH_CLILOC = 1008124

# Confirmed live root cause of PHASE4.md item 4's "Mine/Harvest intermittently
# freezes" bug: a genuine, sustained server-side "no", not a client wedge. Two
# distinct ServUO `HarvestDefinition` messages, both live-reproduced with
# unfiltered journal traces (see docs/PHASE4.md item 4's follow-up note):
#   - "no resource reachable" (mining: cliloc 503040 "There is no metal here to
#     mine.") — `Harvest`'s own reach (`probe_offsets`, Chebyshev 2) sits well
#     inside one 8x8 `HarvestBank` grid cell
#     (`Scripts/Services/Harvest/Core/HarvestBank.cs`), which respawns only
#     after 10-20 real minutes once first consumed. Re-probing a *different*
#     tile in the same ring doesn't help if the whole ring shares that one
#     bank — but a probe ring that straddles a bank boundary can be *partly*
#     exhausted: some tiles keep failing, others still occasionally succeed.
#     That partial case is what a first fix attempt (a strict streak reset by
#     any reward) missed entirely — live-caught in the P0 hardening pass's own
#     6-session gate: sessions whose ring straddled a mostly-dead bank netted
#     2-6 ore over a full 300-tick window (skill gain from the rare live tile
#     kept resetting the streak before it could ever cross a "fully dead"
#     threshold), never triggering the fix meant for exactly this. See
#     `no_resource_clilocs`'s own comment for the windowed-rate redesign.
#   - "no room in the pack" (mining: cliloc 1010481 "Your backpack is full, so
#     the ore you mined is lost.") — the dig still succeeds server-side; the
#     resulting item is discarded because there's no room. `Harvest` alone
#     can't free pack space (that's a smelt/drop/sell skill's job — see
#     `skills/smelt.py::MineAndSmelt`), so this is exactly as unrecoverable by
#     swinging more as the resource-exhausted case — but unlike a bank,
#     nothing about relocating helps a full pack; see `step()`'s own handling.
# Left unhandled, `step()`'s swing/probe branches keep firing every tick
# regardless — the agent isn't dead and the session doesn't crash, it just
# burns the rest of a fixed-window session on discarded digs, which reads from
# the outside exactly like a freeze (pack ore/ingots stopped advancing).
# `Harvest.no_resource_clilocs`/`.pack_full_clilocs` below are opt-in per
# subclass (empty by default, like `catch_cliloc`) since each ServUO harvest
# system uses its own message ids
# (`Scripts/Services/Harvest/{Mining,Lumberjacking,Fishing}.cs`
# `HarvestDefinition.NoResourcesMessage`/`.PackFullMessage`) — `Mine` sets both
# to the live-confirmed mining clilocs; `Chop` already handles wood depletion
# via `NODE_DEPLETED_CLILOC` (the same cliloc as lumberjacking's own
# `NoResourcesMessage`) by cycling its known grove node list, so only
# `pack_full_clilocs` is new for it; `Fish` gets both.

# Once relocation triggers (see `no_resource_clilocs`), walk this offset away
# from the current stand spot before resuming probing — comfortably past
# ServUO's 8-tile `HarvestBank` grid cell width (mirrors `profession.py`'s own
# `MINING_SPOTS` entries, kept >=33 tiles apart for the identical reason).
# Rotates through 8 compass directions (`Harvest._start_relocate`) so repeated
# relocations from the same dead spot don't all try the same blocked direction.
RELOCATE_OFFSETS: list[tuple[int, int]] = [
    (12, 0), (9, 9), (0, 12), (-9, 9), (-12, 0), (-9, -9), (0, -12), (9, -9),
]


class Harvest(Skill):
    """Base gathering skill: use a tool on probed neighbour tiles, reward on skill gain.

    Subclasses set `tool_graphics`, `skill_id`, `name`, `description`.
    """

    tool_graphics: frozenset[int] = frozenset()
    skill_id: int = -1
    #: Some tools must be worn to work (lumberjacking: "the axe must be equipped").
    requires_equipped: bool = False
    #: Worn layer for the tool (2 = two-handed, e.g. an axe).
    equip_layer: int = 2
    #: Tiles to probe round-robin when there's no exact node (reach of the harvest).
    probe_offsets: list[tuple[int, int]] = PROBE_OFFSETS
    #: Cliloc that signals a successful catch (fishing) — rewarded per occurrence.
    catch_cliloc: int | None = None
    #: See the module-level comment above `Harvest` for what these mean and why
    #: they exist. Empty (off) by default — each subclass opts in with its own
    #: ServUO message ids.
    #:
    #: Detection is a **windowed rate**, not a strict all-or-nothing streak: the
    #: last `stuck_window_rotations * len(probe_offsets)` swing outcomes are
    #: kept (`harvest_recent_stuck` — a `deque`, one sample per swing reply,
    #: `1` if it carried a `no_resource_clilocs`/`pack_full_clilocs` cliloc
    #: else `0`), and the trigger is "at least `stuck_rate_threshold` of a full
    #: window is stuck" — a bank that's mostly-but-not-fully exhausted still
    #: yields the rare trickle-through success/skill-gain, which is exactly
    #: what defeated a first fix attempt's strict streak (see the module
    #: comment): a single reward reset the whole counter back to zero, so a
    #: ring that was, say, 70% dead never crossed an unbroken-streak
    #: threshold even after hundreds of ticks. A rate over a rolling window
    #: is robust to that — occasional successes lower the rate without
    #: wiping the window's memory of the many "no" replies around them.
    no_resource_clilocs: frozenset[int] = frozenset()
    pack_full_clilocs: frozenset[int] = frozenset()
    #: How many probe-ring rotations the rate window spans.
    stuck_window_rotations: int = 3
    #: Fraction of a full window that must be "stuck" to act.
    stuck_rate_threshold: float = 0.3
    #: Consecutive unmoved ticks before a relocation `WalkTo` leg is considered
    #: stalled (mirrors `skills/movement.py::GoTo.walkto_stall_limit`) — gives
    #: up and resumes harvesting from wherever it ended up rather than
    #: retrying forever; "somewhere different" is the goal, not an exact tile.
    relocate_stall_limit: int = 6

    def can_run(self, ctx: SkillContext) -> bool:
        return self._tool(ctx) is not None or self._backpack(ctx) is not None

    def diagnose(self, ctx: SkillContext) -> str | None:
        """`None` iff `can_run`, plus a second diagnostic layered on top (mirrors
        `MineSmeltDeliver.diagnose`'s own layering): relocating means this
        skill is technically runnable but is deliberately not harvesting
        right now — the local ground is (mostly) dead and it's walking
        somewhere else instead of continuing to probe it."""
        if not self.can_run(ctx):
            return f"{self.name}: preconditions not met"
        if ctx.memory.get("harvest_relocating"):
            return f"{self.name}: local resources exhausted — relocating to a new spot"
        return None

    def step(self, ctx: SkillContext) -> SkillResult:
        obs = ctx.obs

        # Reward = skill base gained since last tick (the real signal).
        reward = 0.0
        base = self._skill_base(obs)
        prev = ctx.memory.get("harvest_base")
        if base is not None:
            if prev is not None and base > prev + 1e-3:
                reward = base - prev
            ctx.memory["harvest_base"] = base

        # Direct output (fishing): reward each catch, since the skill barely moves.
        if self.catch_cliloc is not None:
            reward += sum(1.0 for j in obs.new_journal if j.cliloc == self.catch_cliloc)

        # A node that ran out of resource → move on to the next one in the cluster.
        if any(j.cliloc == NODE_DEPLETED_CLILOC for j in obs.new_journal):
            ctx.memory["harvest_idx"] = ctx.memory.get("harvest_idx", 0) + 1

        # Already relocating → monitor that instead of the harvest state
        # machine below (see `_relocate_step`'s own docstring). Checked before
        # anything else touches `pending_target`/probing: a relocation leg
        # only ever starts between swings (no cursor open), so there's never a
        # dangling target to worry about here.
        if ctx.memory.get("harvest_relocating"):
            result = self._relocate_step(ctx)
            if result is not None:
                return SkillResult(result.status, result.action, result.reward + reward)
            # Arrived (or gave up) — fall through to resume ordinary
            # harvesting from the new position; `_relocate_step` already
            # cleared the relocation state and the stuck-rate window.

        # See the module-level comment above `no_resource_clilocs` for the
        # windowed-rate design. One sample per swing *reply* — gated on
        # `pending_target is None` (a reply just landed, or nothing has swung
        # yet) so the "Where do you wish to dig?" prompt tick that always
        # precedes it isn't also recorded as a spurious "not stuck" sample
        # (that would silently halve the true rate).
        if (self.no_resource_clilocs or self.pack_full_clilocs) and obs.pending_target is None:
            stuck_this_tick = any(
                j.cliloc in self.no_resource_clilocs or j.cliloc in self.pack_full_clilocs
                for j in obs.new_journal
            )
            ring = max(1, len(self.probe_offsets))
            window = ring * self.stuck_window_rotations
            recent = ctx.memory.get("harvest_recent_stuck")
            if not isinstance(recent, deque) or recent.maxlen != window:
                recent = deque(recent or (), maxlen=window)
            recent.append(1 if stuck_this_tick else 0)
            ctx.memory["harvest_recent_stuck"] = recent

        # 1) Cursor open → target the resource. If the Control plane gave us exact
        #    node(s) (x, y, z, graphic) — required for statics like trees — target
        #    the current one; otherwise probe the surrounding tiles (works for ore).
        if obs.pending_target is not None:
            node = self._current_node(ctx)
            if node is not None:
                x, y, z, graphic = node
                return SkillResult(Status.RUNNING, TargetGround(x=x, y=y, z=z, graphic=graphic), reward)
            p = obs.player.pos
            offs = self.probe_offsets
            dx, dy = offs[ctx.memory.get("harvest_probe", 0) % len(offs)]
            return SkillResult(Status.RUNNING, TargetGround(x=p.x + dx, y=p.y + dy, z=p.z), reward)

        # Between swings: the recent window is full and mostly "no" — every
        # reachable tile shares the same exhausted bank, or the pack has no
        # room. Both trigger the same walk: relocating obviously can't free
        # pack space (the SOURCE is fine, the SINK isn't — see the module
        # comment), but it's harmless there too, not actively wrong — no
        # worse than staying put with a full pack, and the window doesn't
        # distinguish *which* cliloc caused it, so there's nothing to gain
        # from special-casing pack-full here rather than in the (still open)
        # follow-up of teaching a sibling skill to drop/smelt on relocate.
        recent = ctx.memory.get("harvest_recent_stuck")
        if recent is not None and len(recent) == recent.maxlen and (
            sum(recent) / len(recent) >= self.stuck_rate_threshold
        ):
            return self._start_relocate(ctx, reward)

        tool = self._tool(ctx)
        if tool is not None:
            ctx.memory["harvest_tool"] = tool.serial  # remember it (vanishes on cursor)

        # 2) Equip the tool if it must be worn (lumberjacking). A UO equip is two
        #    steps — pick up to the cursor, then wear — and the item disappears
        #    from `items` while held, so drive it off the remembered serial.
        if self.requires_equipped and not (tool is not None and tool.layer == self.equip_layer):
            tser = ctx.memory.get("harvest_tool")
            if tser is None:  # never seen the tool → open the pack to reveal it
                bp = self._backpack(ctx)
                if bp is None:
                    return SkillResult(Status.FAILURE, None, reward)
                return SkillResult(Status.RUNNING, Use(serial=bp.serial), reward)
            step = ctx.memory.get("harvest_equip_step", 0)
            ctx.memory["harvest_equip_step"] = (step + 1) % 2
            if step == 0:
                return SkillResult(Status.RUNNING, PickUp(serial=tser, amount=1), reward)
            return SkillResult(Status.RUNNING, Equip(serial=tser, layer=self.equip_layer), reward)

        # 3) Tool not visible (and not mid-equip) → open the pack to reveal it.
        if tool is None:
            bp = self._backpack(ctx)
            if bp is None:
                return SkillResult(Status.FAILURE, None, reward)
            return SkillResult(Status.RUNNING, Use(serial=bp.serial), reward)

        # 4) Swing — and advance the probe so the next attempt tries the next tile.
        ctx.memory["harvest_probe"] = ctx.memory.get("harvest_probe", 0) + 1
        return SkillResult(Status.RUNNING, Use(serial=tool.serial), reward)

    def _start_relocate(self, ctx: SkillContext, reward: float) -> SkillResult:
        """Kick off a relocation leg: pick the next `RELOCATE_OFFSETS` entry
        (rotating so repeated relocations from the same dead spot don't all
        try the same blocked direction), issue one `WalkTo`, and switch into
        monitoring mode (`_relocate_step`, mirroring `GoTo`'s own "issue once,
        then watch position deltas" contract for the non-blocking route
        driver — see that class's docstring for why distance-to-target isn't
        the progress signal). The stuck-rate window resets: the new stand
        spot's own tiles haven't been sampled yet.
        """
        ctx.memory["harvest_recent_stuck"] = None
        idx = ctx.memory.get("harvest_relocate_idx", 0)
        ctx.memory["harvest_relocate_idx"] = idx + 1
        dx, dy = RELOCATE_OFFSETS[idx % len(RELOCATE_OFFSETS)]
        here = ctx.obs.player.pos
        tx, ty = here.x + dx, here.y + dy
        ctx.memory["harvest_relocating"] = True
        ctx.memory["harvest_relocate_target"] = (tx, ty)
        ctx.memory.pop("harvest_relocate_last_pos", None)
        ctx.memory["harvest_relocate_stall"] = 0
        return SkillResult(Status.RUNNING, WalkTo(x=tx, y=ty), reward)

    def _relocate_step(self, ctx: SkillContext) -> SkillResult | None:
        """One tick of relocation monitoring. Returns a `SkillResult` to act
        on (idling while the route advances on its own — `WalkTo` is
        fire-and-forget, driven by the body's own pump cadence, same as
        `GoTo`), or `None` once arrived (exact tile) or stalled
        (`relocate_stall_limit` unmoved ticks) — either way, resume ordinary
        harvesting: an exact destination was never the point, just
        "somewhere different"; a route that can't get there at all is no
        worse than the pre-fix behaviour (stays put and keeps trying).
        """
        here = ctx.obs.player.pos
        tx, ty = ctx.memory["harvest_relocate_target"]
        if chebyshev(here, Position(tx, ty, here.z)) == 0:
            self._clear_relocate(ctx)
            return None
        cur = (here.x, here.y)
        last = ctx.memory.get("harvest_relocate_last_pos")
        if last is None or cur != last:
            ctx.memory["harvest_relocate_last_pos"] = cur
            ctx.memory["harvest_relocate_stall"] = 0
            return SkillResult(Status.RUNNING, None)
        stall = ctx.memory.get("harvest_relocate_stall", 0) + 1
        ctx.memory["harvest_relocate_stall"] = stall
        if stall >= self.relocate_stall_limit:
            self._clear_relocate(ctx)
            return None
        return SkillResult(Status.RUNNING, None)

    @staticmethod
    def _clear_relocate(ctx: SkillContext) -> None:
        for key in (
            "harvest_relocating", "harvest_relocate_target",
            "harvest_relocate_last_pos", "harvest_relocate_stall",
        ):
            ctx.memory.pop(key, None)

    def _current_node(self, ctx: SkillContext):
        """The node to harvest now: cycle a cluster (`harvest_nodes`) if given,
        else a single `harvest_node`, else None (probe)."""
        nodes = ctx.memory.get("harvest_nodes")
        if nodes:
            return nodes[ctx.memory.get("harvest_idx", 0) % len(nodes)]
        return ctx.memory.get("harvest_node")

    def _skill_base(self, obs) -> float | None:
        return next((s.base for s in obs.skills if s.id == self.skill_id), None)

    def _tool(self, ctx: SkillContext):
        return next((i for i in ctx.obs.items if i.graphic in self.tool_graphics), None)

    @staticmethod
    def _backpack(ctx: SkillContext):
        # Filter by owner, not just layer: a nearby mobile's own backpack also has
        # layer == BACKPACK_LAYER, and — being a *contained* item — both report the
        # same placeholder (0,0,0) position, so they can tie on distance with ours
        # and sort first (live-observed at a crowded mining spot). `container` is
        # always the wearer's mobile serial for a worn item (anima-core `net/game.rs`
        # `equip_update`/`mobile_incoming`), so it's the reliable way to pick *our*
        # pack out of everyone else's.
        return next((i for i in ctx.obs.items
                    if i.layer == BACKPACK_LAYER and i.container == ctx.obs.player.serial), None)


class Mine(Harvest):
    """Mine ore from surrounding rock with a pickaxe."""

    name = "mine"
    description = "Mine ore from the surrounding rock with a pickaxe."
    tool_graphics = PICKAXE_GRAPHICS
    skill_id = SKILL_MINING
    # ServUO `Mining.cs` `oreAndStone.NoResourcesMessage`/`.PackFullMessage` —
    # live-confirmed root cause of the freeze (see the module-level comment).
    no_resource_clilocs = frozenset({503040})  # "There is no metal here to mine."
    pack_full_clilocs = frozenset({1010481})  # "Your backpack is full, ..."


class Chop(Harvest):
    """Chop logs from a tree with an axe (lumberjacking).

    The axe must be **equipped** (worn, two-handed) and the target must be the
    exact tree static — so a lumberjack needs `memory['harvest_node']` = (x, y, z,
    graphic) set by the Control plane (found via `anima2.uomap.find_trees`).
    """

    name = "chop"
    description = "Chop logs from a tree with an axe."
    tool_graphics = AXE_GRAPHICS
    skill_id = SKILL_LUMBERJACKING
    requires_equipped = True
    equip_layer = 2  # two-handed
    # No `no_resource_clilocs`: lumberjacking's own "no resources" message
    # (`Lumberjacking.cs` `lumber.NoResourcesMessage`) is cliloc 500493 —
    # already handled above as `NODE_DEPLETED_CLILOC`, which cycles the known
    # grove node list rather than relocating (a grove is a short, known
    # cluster reached via exact nodes, not an unbounded probe ring — the
    # windowed-rate/relocate machinery only ever engages in the probe branch,
    # via `no_resource_clilocs`, which `Chop` leaves empty). `pack_full_clilocs`
    # is new for it.
    pack_full_clilocs = frozenset({500497})  # "You can't place any wood into your backpack!"


class Fish(Harvest):
    """Fish from water with a fishing pole (no equip needed; casts up to 4 tiles).

    Water is contiguous *terrain*, so — unlike trees — a fisher just probes the
    tiles in casting range (graphic 0 land target). Stage it on a shore tile from
    `find-water` (anima-net) and it casts at the nearby water.
    """

    name = "fish"
    description = "Fish from the nearby water with a fishing pole."
    tool_graphics = POLE_GRAPHICS
    skill_id = SKILL_FISHING
    probe_offsets = FISH_OFFSETS  # reach 4
    catch_cliloc = CATCH_CLILOC  # reward = fish caught
    # ServUO `Fishing.cs` `fish.NoResourcesMessage`/`.PackFullMessage`.
    no_resource_clilocs = frozenset({503172})  # "The fish don't seem to be biting here."
    pack_full_clilocs = frozenset({503176})  # "You do not have room in your backpack for a fish."
