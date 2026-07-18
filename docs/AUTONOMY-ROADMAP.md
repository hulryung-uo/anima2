# Autonomy Roadmap — From Staged Worker to UO AI Player

Last updated: 2026-07-18

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
interrupts now act, but LLM goals are limited to idle/nearby goto; curriculum picks are
observational; skill retrieval does not steer the planner. Expanding the
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
4. **GM-free resurrection discovery.** Parse ServUO healer/corpse waypoints
   (`0xE5/0xE6`) instead of relying on staged coordinates.

### B. Intention and planning

1. Add an interrupt/resume goal stack with progress, completion, and deadlines.
2. Connect curriculum milestones to closed-vocabulary goals (opt-in first).
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
recovery whitelist. The healer coordinate is deliberately fixture-only:
production planners pass no coordinate and safely quarantine if no gump is
already available. GM-free healer discovery remains A4 via `0xE5/0xE6`.

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
