# Autonomy Roadmap — From Staged Worker to UO AI Player

Last updated: 2026-07-17

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
list after the control plane grants skills, tools, and a workplace. Reflexes do
not yet act; LLM goals are limited to idle/nearby goto; curriculum picks are
observational; skill retrieval does not steer the planner. Expanding the
evolution budget before closing those loops would optimize configuration more
than autonomy.

## Work order

### A. Survival and continuity

1. ✅ **Flee then bandage (first vertical slice).** Below 40% HP, retreat from a
   nearby hostile group; once contact breaks (or bounded retreat attempts are
   exhausted), apply a bandage with `Use -> TargetObject(self)` and confirm the
   result from HP/journal observations. This uses the existing contract.
2. **Poison and death contract.** Expose the body's already-known
   `poisoned`/body state through Rust `PlayerView`, JSON, and Python. Add cure,
   resurrection-gump acceptance, and own-corpse recovery.
3. **Resilient body lifecycle.** Preserve Agent goal/memory/ticks while a body
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
  cursors, issues one bandage per attempt, and clears or bounds every state.
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
an observed HP increase; poison/death state becomes explicit in A2. The
hunter profession now carries bandages and the Healing/Anatomy baseline needed
to exercise it in ordinary village runs.

Offline: 672 tests green, Ruff clean. Live `anima-client` + ServUO gate: the
isolated heal leg passed, then one hostile leg proved bounded running retreat,
increased hostile distance, ordered `flee -> Use -> TargetObject(self)`, one
bandage consumed, and confirmed HP recovery — all passed.
