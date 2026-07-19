# Autonomy Roadmap — From Staged Worker to UO AI Player

Last updated: 2026-07-19

## Objective

Build an agent that logs in through `anima-client`, survives, chooses and
completes goals by composing verified skills, recovers from ordinary failure,
and continues the same life across sessions without routine GM intervention.
The GM control plane remains an evaluation fixture, never a dependency of
normal play.

## Current gap

The body/brain contract, deterministic skills, live ServUO gates, economy
loops, memory, and measurement kernel are strong foundations. The production
agent is still a staged worker: `Profession.planner()` runs a fixed priority
list after the control plane grants skills, tools, and a workplace. Survival
interrupts now freeze and preserve a durable goal stack, and the opt-in B2 path
turns trusted curriculum picks into profession-bound work goals. The broader
cognition vocabulary, self-provisioning, workplace-return policy, and dynamic
skill retrieval still do not steer the planner. Expanding the
evolution budget before closing those loops would optimize configuration more
than autonomy.

## Work order

### A. Survival and continuity

1. ✅ **Flee then bandage (first vertical slice).** Below 40% HP, retreat from a
   nearby hostile group; once contact breaks (or bounded retreat attempts are
   exhausted), apply a bandage with `Use -> TargetObject(self)` and confirm the
   result from HP/journal observations. This uses the existing contract.
2. ✅ **Poison and death contract.** Expose the body's already-known
   `poisoned`/body state through Rust `PlayerView`, JSON, and Python. Add cure,
   resurrection-gump acceptance, and own-corpse recovery.
3. ✅ **Resilient body lifecycle.** Preserve Agent goal/memory/ticks while a body
   wrapper restarts a failed IPC bridge with bounded backoff.
4. ✅ **GM-free resurrection discovery.** Parse ServUO healer/corpse waypoints
   (`0xE5/0xE6`) instead of relying on staged coordinates.

### B. Intention and planning

1. ✅ Add an interrupt/resume goal stack with progress, completion, and deadlines.
2. ✅ Connect curriculum milestones to closed-vocabulary goals (opt-in first).
3. Select only verified skills already allowed by the profession; never execute
   arbitrary LLM-authored code or switch a non-yieldable skill mid-transaction.
4. Expand cognition from idle/goto to validated work, acquire, sell, bank,
   recover, explore, assist, and socialize goals.

### C. Self-provisioning

Reduce control-plane help in measured steps: fully staged -> location only ->
start town only -> ordinary fresh character. The agent must find or buy tools,
discover work/vendor/bank locations, earn starter gold, and replace broken
equipment through normal play.

### D. Liveness and recovery

Track observation-derived progress (position, HP, gold, inventory, skill base,
goal progress). A bounded watchdog resets only stale transient state, then
tries an alternate verified skill, safe movement, and finally wander/reconsider.
Do not port v1's procedure-diversity heuristic: a healthy long Mine/Hunt run is
supposed to repeat one skill.

### E. Learning and evolution

Use the independent kernel after a real decision consumes the learned signal.
Tune concrete bottlenecks such as retreat thresholds, rest timing, stock
targets, exploration radius, and retry policy. Re-run evolution versus random
only when every searched axis changes a meaningful live trajectory; a larger
budget alone is not an autonomy milestone.

## Acceptance ladder

- Offline: survival preempts work, rejects incompatible unrelated target
  cursors, issues one bandage per attempt, and bounds every active transaction.
  A ghost without a discovered resurrection source remains safely quarantined
  without action spam until the world changes.
- Live staged: a wounded character increases hostile distance, applies a
  bandage once, and shows confirmed HP recovery without manual input.
- Live failure injection: tool break, full pack, depleted resource, blocked
  path, death, and bridge loss each recover within a bounded window.
- Capstone: an ordinary character runs for multiple hours without GM commands,
  completes an economy goal, survives/reconnects, and resumes its persisted
  intention after restart.

Primary metrics: intervention count, autonomous uptime, recovery rate/time,
goal completion rate, replans per goal, normal-play gold/resource production,
and cross-session continuity. Test count remains a regression measure, not the
definition of autonomy.

## Landed

### A1 — flee then bandage ✅

`skills/survival.py::Survive` is the first skill in every profession planner.
It is inert unless the character is below 40% HP and can either flee a nearby
hostile or use a bandage. It shares `combat.py::is_hostile` with combat, emits
bounded `Walk(run=True)` steps away from the observed hostile centroid, never
hijacks an incompatible target cursor opened by work, and runs a single `Use ->
TargetObject(self) -> HP confirmation` bandage attempt at a time. Completion
journal messages resolve the attempt but never create a success signal without
a nearby observed HP increase. This avoids mistaking T2A natural regeneration
for the bandage and restarting the server-side timer. The
hunter profession now carries bandages and the Healing/Anatomy baseline needed
to exercise it in ordinary village runs.

Live `anima-client` + ServUO gate: the
isolated heal leg passed, then one hostile leg proved bounded running retreat,
increased hostile distance, ordered `flee -> Use -> TargetObject(self)`, one
bandage consumed, and confirmed HP recovery — all passed.

### A2 — poison and death continuity ✅

The version-7 body contract now carries `body`, `poisoned`, and derived `dead`
from Rust world state through native JSON into Python, with a native ready-event
schema handshake. ServUO's complete seven-body ghost set is shared by the Rust
agent/scene path and the web renderer. Structured gump elements and
`TargetCancel` are also preserved end to end.

`Survive` cures poison even at full HP when Healing and Anatomy meet the ServUO
floor, flees from a single nearby hostile before curing, accepts only an
observation-confirmed poison clear, and backs off every failed cure path.
`RecoverDeath` stops stale routes and cursors, quarantines ordinary work while
dead, accepts only the structurally verified free resurrection gump, confirms a
living observation, and reclaims items only from a uniquely attributed corpse.
Attribution uses the pre-death body/position plus observed equipment or pack
serial continuity; ambiguity and unverified drops fail closed. A second death
starts a fresh episode and atomically discards the earlier recovery transaction.
Neither interrupt consumes the active Goal.

Offline: 705 tests green, Ruff clean. The staged live fixture completed poison
cure in 40/60 ticks and death -> verified free resurrection -> exact GM-readback
corpse -> pre-death item returned to backpack -> same two-step Goal resumed in
67/240 ticks at a 400 ms pump. Every action emitted while dead matched the
recovery whitelist. At the A2 milestone the healer coordinate was deliberately
fixture-only: production planners passed no coordinate and safely quarantined
if no gump was already available. A4 below closes that discovery gap via
`0xE5/0xE6`.

### A3 — resilient body lifecycle ✅

`ResilientIpcBody` is a stable body identity around the replaceable
`IpcBody`/`anima-agent` child. A bridge EOF, broken pipe, or bounded response
timeout starts an immediate retry followed by capped exponential backoff. Each
replacement must report the original player serial in both its ready event and
first observation. A single-owner account lease prevents competing supervisors,
rapid crash loops share a retry budget until the session proves stable, and an
absolute outage deadline covers production ready/validation RPCs.

The transport now distinguishes protocol, remote-request, transport, ownership,
and exhausted-recovery failures. A dedicated reader thread makes even a partial
NDJSON line obey the response timeout; every failed child is killed and reaped,
unexpected factory/schema errors fail closed, and close is terminal across
concurrent or re-entrant recovery. Actions known to have failed before write are
sent once after recovery. Actions whose flush or acknowledgement is ambiguous
are never replayed and increment `uncertain_actions`, preserving at-most-once
safety for purchases, item moves, gump responses, and speech.

`live.py`, `fleet.py`, and `village.py` now use the resilient supervisor while
keeping the Python `Agent`, goal, memory, episodic history, planner, and tick
counter intact. Fleet and village runners close every successfully created body
on normal exit, partial login failure, and downstream exceptions.

Offline: 729 tests green, Ruff clean, including partial-line hangs, absolute
deadlines, crash-loop budgets, identity mismatch, uncertain actions, unexpected
factory failures, and concurrent/re-entrant close. The live failure-injection
gate killed PID 73111 during an active `GoTo`, reconnected the same serial 17363
as PID 73214/generation 2 in 0.937 seconds, preserved the exact Agent/body/Goal/
memory/episodes/ticks state, emitted no action during reconnect, re-issued the
same target `WalkTo` within 6 resumed ticks, resumed real movement, and arrived
at the destination. All 22 gate flags passed without a GM connection during
recovery.

### A4 — GM-free resurrection discovery ✅

The version-8 body contract now carries ServUO `0xE5` waypoints end to end:
Rust parses the exact big-endian packet header plus signed Z, facet, kind,
ignore-object flag, cliloc, and UTF-16LE name; `0xE6` removes by serial; and
`Observation.waypoints` is deterministically sorted by distance then serial.
Facet changes clear the set. Ordinary mobile interest-range deletes preserve
distant healer markers, while a deleted corpse removes only its corpse marker.

`RecoverDeath()` no longer needs a production resurrection coordinate. While
dead it selects the nearest same-facet type-6 waypoint, follows the referenced
mobile's current position unless the marker says to ignore the object, and
carries that selection across an A3 bridge replacement whose fresh world has
no re-sent waypoint. The route watchdog boundedly reissues long `WalkTo` routes
after the bridge's 200-step route budget. A healer that never presents the
structurally verified free resurrection gump is cooled down and the next
candidate is tried; priced or otherwise unverified gumps remain rejected.

Corpse waypoints are deliberately only navigation hints. Current ServUO can
send no corpse marker to this reported client version, or can send a previous
corpse because of its death-time ordering. A marker is therefore cached only
when it matches the frozen death position, and item recovery still requires
the A2 body/position plus equipment or backpack-serial continuity proof.

Offline: 745 Python tests and the full Rust workspace pass; Ruff and Rust
format checks are clean. The live schema-v8 gate staged and killed the subject,
then closed the GM connection before the first recovery tick. With
`RecoverDeath()` receiving no coordinate, the agent observed the exact healer
E5, selected it by serial, emitted `WalkTo` to that observed location, moved as
a ghost, accepted one verified free gump, observed life plus healer E6 removal,
opened the exact strongly attributed corpse once, recovered the same pre-death
dagger serial, and resumed the original Goal in 15 ticks. All 23 strengthened
flags passed, including unchanged bridge generation/facet through E6 and the
exact dagger's observed `corpse -> backpack` transition.

One narrow follow-up remains: ServUO does not resend death waypoints when a
brand-new bridge logs in. A bridge replacement after a healer was selected is
covered by the episode cache; a process that first attaches after death and
before ever observing E5 must stay safely quarantined until another trusted
discovery source is added.

### B1 — durable intention stack ✅

`GoalStack` now wraps the existing `Goal` object without copying it. Every live
frame has a stable id, source, lifecycle state, absolute tick deadline, immutable
observation-derived progress snapshot, and bounded terminal history. Explicit
interrupts are LIFO: the exact parent frame becomes suspended, child success,
failure, cancellation, or expiry archives only that child, and the same parent
object resumes. A deadline sweep also expires buried parents, so an interrupt
cannot keep obsolete work alive indefinitely. `Agent.goal` remains a compatible
view/setter for existing launchers.

`goto` progress comes only from observed positions. It records real movement,
normalizes the best observed distance without regressing on a healthy A* detour,
and freezes while an explicit child or deterministic survival/death recovery
owns the hands. Every top-frame transition stops an in-flight native `WalkTo`
before the next frame acts, including routes emitted by non-`goto` skills.

Cognition is proposal-only while a goal stack is live. Background results carry
an agent-unique intention token plus monotonic revision; interrupt/resume ABA,
progress, deadline, terminal, and safety transitions invalidate older answers.
Worker contexts copy mutable memory, completed decisions are delivered once, and
the only cognition side effect (`pending_say`) is committed by the fast loop
under the same token check. A late LLM answer therefore cannot erase a
transaction, cross from one Agent to another, resurrect completed work, or leak
stale speech.

Offline: 765 tests pass, including nested identity-preserving resume, child
success/failure, buried deadlines, bounded depth, fail-closed frame ids, native
route cancellation, cognition ABA/cross-agent isolation, positional API
compatibility, and death/corpse-progress freezing. Ruff and diff checks are
clean.

Live `anima-client` + ServUO gate: after the GM staged one subject and closed,
base frame 1 began the calibrated 36-tile A* course. Child frame 2 stopped the
old route, moved to its own observed target, succeeded once, and resumed the
same parent/progress. Child frame 3 then stopped the parent route, issued a real
route, expired exactly once at its two-tick deadline, stopped that route, and
again resumed frame 1. The parent reissued its original target, moved for 114
ticks, and completed once. Alternating `None`/foreign-goto cognition ran on all
134 ticks and was rejected 134 times; all 20 gate flags passed with no GM
connection during work. The A4 death gate was rerun afterward and all 23 flags
still passed in 15 ticks, proving resurrection, corpse recovery, and original
Goal continuity remain intact. After the final first-tick safety pre-emption
fix, the A1 live gate also reran cleanly: all 17 flee/bandage flags passed.

### B2 — closed curriculum work goals ✅

`--curriculum-goals` is a separate opt-in from the original observational
`--curriculum`. The controller may construct only the exact schema
`Goal(kind="curriculum", params={"schema": 1, "profession": ..., "milestone":
...})`; milestone and profession must match the hand-written catalog. The LLM
never supplies a Goal kind, action, coordinate, or skill name. `Agent` applies a
second context-aware admission check before a cognition proposal can enter the
stack. The controller also preserves an inner `ThreadedCognition` decision's
original intention token and pending speech, closing the wrapper seam that
could otherwise have laundered a stale result into a fresh proposal.

An opt-in profession planner binds that Goal to exactly its existing work-skill
instance. Without an admitted Goal it waits in place instead of wandering away
from a calibrated workplace; with a valid Goal whose tool/preconditions are
temporarily missing it also waits. Inventory milestones temporarily raise the
trusted skill's consume/sell threshold (`20` ore, `10` daggers) and restore the
normal threshold afterward. Completion comes only from the catalog predicate
and only at an observation-confirmed FSM yield point: no open target cursor,
unsafe craft/vendor UI, lifted item, smelt/delivery/market leg, or relocation.
The visible blacksmith MAKE_LAST prompt is an explicit quiescent boundary; its
same `loop` state while the prompt is absent remains non-yieldable. Once a
profession's catalog is exhausted, legacy work resumes instead of idling
forever.

Catalog progress is merged monotonically into the exact `GoalFrame`; non-finite
values fail closed. An invalid inner proposal cannot starve trusted curriculum
work. Autonomous `goto` is deliberately disabled while goal-driving is enabled:
B2 has no durable return-to-workplace policy yet, so allowing even a bounded
excursion could strand the next mining/crafting/fishing Goal away from its
tools. Explicit user/system Goals retain their existing authority.

Offline: 803 tests pass. The B2 adversarial suite covers exact-schema and
cross-profession rejection, default opt-out parity, idle/precondition waiting,
threshold override and restoration, unsafe-FSM completion denial, stale-token
preservation, invalid-proposal starvation, monotonic progress, and NaN/Infinity
rejection. Ruff and diff checks are clean.

Live `anima-client` + ServUO gate: the GM staged a miner at the calibrated ore
spot with Mining 51, 20 ore, exactly 9 ingots, one pickaxe, and a visible forge,
then disconnected before constructing the controller or Agent. The sole
unachieved milestone produced one validated frame; no profession work ran before
that frame. The bound shipped skill emitted a real `Use(ore)` followed by
`TargetObject(exact forge)`, live observations showed ore decrease and ingots
cross `9 -> 14`, the exact frame archived `SUCCESS` once, one milestone episode
was recorded, and settle ticks did not re-enqueue it. All 14 gate flags passed.
