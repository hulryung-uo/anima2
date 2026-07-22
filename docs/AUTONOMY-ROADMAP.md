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
3. ✅ Select only verified skills already allowed by the profession; never execute
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

### B3 — immutable verified capability registry + bank goal ✅

B3 deliberately does not reuse the retrieval-oriented `skill_library.REGISTRY`:
that catalog includes base/superset and safety/social skills whose metadata is
not execution authority. `capabilities.py` instead owns an immutable registry of
opaque `(profession, capability)` ids. Each frozen binding supplies one shipped
leaf skill type, allowed Goal sources, readiness, completion, progress, safe
yield, and a default deadline. Duplicate/dynamic/LLM-authored classes, module
paths, graphics, attributes, and free-form arguments are not part of the wire
schema.

The first genuinely new entry is `("blacksmith", "bank_gold")`, requested only
as `Goal(kind="capability", params={"schema": 1, "profession": "blacksmith",
"capability": "bank_gold"})`. Admission verifies exact keys/types, source,
profession, configured banker route, current pack gold, and idle UI state. It
then creates a separate, deeply sealed Goal whose kind, nested parameters, and
seal authority cannot be mutated by the proposal owner, and applies a 120-tick
deadline. Agent startup requires the exact frozen `CapabilityPolicy` type and
checks that its profession/capability fingerprint exactly matches the planner;
duck-typed policies, wrapped callbacks, and foreign/missing handlers fail before
the first tick.

The registry binds only `BankGold`, an operation-specific adapter over the
already-live-verified `BlacksmithMarket` bank FSM. It can request/select the
banker menu, wait for the bank box to synchronize, lift/drop gold, and return;
it cannot craft, sell, or retrieve another skill by name. The exact leased
instance remains bound to the sealed frame; raw/malformed capability Goals fall
through to a no-action wait rather than Wander. Confirmed bank-box balance is
the only progress/completion evidence, and completion waits for the transaction
to return to an idle yield point. At the registry deadline an idle frame expires
immediately; a frame between `PickUp` and `Drop` retains only the narrow lease
needed to drain that in-flight transaction, then expires at its first safe yield
point. It cannot begin another attempt after returning idle.

Offline: 865 tests pass. The new adversarial suite covers registry uniqueness
and immutability, exact schema/injection/cross-profession rejection, source
authority at the resolver (`COGNITION`/`USER`/`SYSTEM` allowed, `SKILL` denied),
detached deep sealing, policy/callback forgery, planner-policy mismatch, direct
Agent API bypasses, opt-out parity, one-handler binding, no-action invalid
fallback, deadline installation, safe transaction drain, safe expiry, and
expired-proposal replay rejection independent of bounded telemetry history.
Direct cancel/replace/interrupt operations also require a current safe-yield
observation. Capability cognition and planner selection each read detached,
deep-copied world/memory snapshots; admission and the shipped skill retain the
authoritative originals, with only an explicitly returned speech line allowed
back across the cognition boundary. The canonical Goal is likewise never shared
with either collaborator. Agent accepts only the exact factory `Planner`, keeps
strong references plus state fingerprints for every installed skill, and checks
the chosen object on every policy tick—even when no Goal is active. Context,
metadata, helper-method, or returned-skill substitution therefore cannot unlock
a bound or unrelated action.

Missing banker readiness drains back to idle. A disappearing bank box after
`PickUp` first emits a compensating `Drop` to the backpack; if the bank may have
accepted an uncertain Drop before reconnect, the adapter reopens it while
retaining ownership. Release is confirmed by the exact serial or by goal-scoped
pack/bank aggregate deltas, so ServUO stack merging cannot strand the lease. The
B3 Agent integration deliberately admits autonomous capability frames only
through cognition today; user/system resolver authority remains available for a
future explicit admitted-workflow API. Ruff and diff checks are clean.

Live `anima-client` + ServUO gate: after the GM deleted fresh-character gold,
staged one exact 100-gold proof stack and one banker, and disconnected, a
cross-profession request was rejected with zero actions and unchanged gold. The
valid request then created one detached, authority-sealed cognition frame with
the registry's exact 120-tick budget and selected the registry's exact instance.
The strengthened gate maps each transaction action back to the owning exact
factory `CapabilityBoundSkill(BankGold)` selection and rejects every additional
action in the capability slice. Live actions followed
`PopupRequest(exact banker) -> PopupSelect(exact
Bank cliloc entry) -> PickUp(exact gold serial, 100) -> Drop(same serial,
observed own bank box)`. Observations showed the same serial move backpack to
bank box, pack `100 -> 0`, bank `0 -> 100`; no hammer `Use`, craft response, or
`SellItems` occurred. The FSM returned idle, the frame archived `SUCCESS` once,
and settle ticks did not re-enqueue it. All 15 flags passed in 14 Agent ticks.

This is still intentionally narrow. `sell_daggers` needs goal-scoped
confirmed-sale evidence first. Acquire/tool replacement, worker exploration,
assist, and verified dialogue remain deferred until their completion and
return/yield contracts are real.

### B4 — closed capability cognition selector ✅

`CapabilityCognition` is a separate opt-in instead of an expansion of the
legacy, permissive `LLMCognition` parser. The provider may return exactly one
complete JSON object in one of two schemas:
`{"schema":1,"decision":"idle"}` or
`{"schema":1,"decision":"capability","capability":"<opaque id>"}`.
Duplicate keys, extra fields, prose, multiple objects, non-finite constants,
old/future schemas, coerced types, case/whitespace variants, unknown ids, and
oversized responses all produce no Goal. The wire format has no Goal kind,
action, coordinate, arguments, source, binding, class/module path, or deadline.
Model-invalid output does not trigger a heuristic interpretation. A missing
client or provider-call exception uses the first registry-ordered ready id as
the offline liveness fallback.

Candidate discovery is advisory and reads only immutable registry bindings
whose source and readiness predicates pass against the detached cognition
snapshot. Trusted code then constructs a new unsealed exact-shape request;
`Agent` independently rechecks current live readiness, creates the distinct
privately sealed canonical frame, and owns its 120-tick deadline. A readiness
change between selection and delivery therefore fails closed. While any Goal
is active the selector returns it without calling the provider; it cannot
create speech or a second choice between `PickUp` and `Drop`, so priority
`SpeakPending` cannot starve transaction hands.

Village wiring is behind default-off `--capability-goals`. In the current
registry only the first miner+blacksmith trade pair has the calibrated banker
route needed by `bank_gold`; that blacksmith receives the exact capability
planner, frozen policy, and `ThreadedCognition(CapabilityCognition(...))` (plus
the existing reflection wrapper in tiered mode). The Control plane explicitly
adds `Gold 100` for that operation instead of depending on shard-specific
fresh-character wealth; capability hands deliberately cannot craft/sell their
own prerequisite yet. The online roster is checked again after partial login
failures, before GM staging, so a lost half-pair cannot silently downgrade to
legacy behavior. Unsupported roster members keep their legacy planner/cognition.
A solo blacksmith and combinations with curriculum modes fail before opening a
body instead of silently waiting forever. `--account-prefix` permits isolated,
repeatable first-run account sets while keeping the historical `anima*` default.
The chronicle detector also recognizes the capability adapter's confirmed
`bank -> craft` payout without weakening the legacy `bank -> bank_return`
evidence path.

Offline: 922 tests pass, Ruff and diff checks are clean. The new suite covers
strict parsing, ready/active zero-call behavior, deterministic provider-failure
fallback, prompt opacity, stale-snapshot admission recheck, canonical cognition
frame/deadline/source, village flag preflight/propagation, chronicle integration,
post-login pair validation, exact runtime/Agent construction, prerequisite
staging, multi-stack reward accounting, and opt-out regression.

Live `anima-client` + ServUO gate: production
`ThreadedCognition(CapabilityCognition(...))` first received an otherwise-valid
reply polluted with an `action` field. It completed with zero Goal, transaction
action, or gold change. Its next exact `bank_gold` choice created one privately
sealed COGNITION frame with the registry deadline. The provider was called only
three times before/during admission and never during the owned transaction. The
same exact 100-gold serial then followed the B3-owned four-action path into the
observed bank box, pack `100 -> 0`, bank `0 -> 100`, returned idle, archived one
SUCCESS, and did not replay. All 16 B4 flags passed in 15 Agent ticks; the
unchanged B3 mode was rerun afterward and all 16 current gate flags passed in
14 ticks.

The actual production village CLI was then run—not a direct Agent fixture—with
a fresh account prefix, one miner+blacksmith pair, `--capability-goals`, 60
ticks, and the real chronicle. The paired smith independently banked both the
shard's 1000 starting gold and the explicit 100 proof gold. Its observed episode
reward reached 1100, and the flushed `banked_gold` event recorded exactly 1100.
That live run exposed and fixed the earlier single-last-stack chronicle
undercount; bank-phase rewards are now accumulated until the verified return to
idle.

B4 closes selection, not the whole life loop. `bank_gold` is still the only
installed operation and its banker/gold prerequisites are staged. Craft, sell,
acquire/replace tools, recover work location, explore, assist, and socialize
must each land as separately evidenced capabilities before this becomes a
self-sustaining player.

### B5 — verified dagger sale + sale→bank composition ✅

The registry now exposes a second opaque operation,
`("blacksmith", "sell_daggers")`, before `bank_gold`. `SellDaggers` is a leaf
adapter over the already verified vendor FSM and can only request/select the
Sell context entry, answer the observed sell list with dagger lines, and return
to idle. It never falls through to crafting or banking. Readiness requires a
configured vendor route, an idle UI/FSM, a backpack, and at least five observed
daggers.

Completion evidence is scoped to the active `goal_id`; an earlier sale cannot
satisfy a later frame. The adapter records every exact offered dagger serial,
quantity, and quoted unit price only when it emits `SellItems`, then requires
all offered serial quantities to disappear and the full quoted gold delta in
later Observations, plus a completed safe return. Aggregate inventory totals
alone are insufficient. Gold
alone, dagger removal alone, an attempted packet, stale memory, or a busy vendor
UI cannot complete the frame. Once its hands are finished, stale UI cannot hold
preemption or expiry forever. A failed trip is not replayed inside the same
frame; its 180-tick deadline may expire at that safe yield. Return success also
requires the observed player position to equal the recorded work stand; a
stalled return is a safe failure, never a false homecoming.

Capability village staging adds five daggers as explicit first-sale inventory
and retains the explicit 100 gold bank prerequisite. With both ids ready, the
deterministic offline selector chooses the registry's sale first and then banks.
The chronicle sale detector now accumulates sale rewards across observation
ticks and accepts a co-located direct `sell -> craft` return; this was caught
live when the server exposed gold growth before dagger removal and the smith was
already within vendor reach.

Expired-request replay protection now tombstones only the exact producer-owned
request object. That stale object cannot refresh its deadline even after goal
history eviction, while a newly constructed equal-valued decision can start a
legitimate retry.

Offline: 947 tests pass, Ruff, compile, and diff checks are clean. The B5 live
gate used production `ThreadedCognition(CapabilityCognition(...))`: an
extra-field response produced no Goal or action, while the exact choice created
one sealed 180-tick COGNITION frame. The exact five staged dagger serials followed
`PopupRequest -> PopupSelect(Sell) -> SellItems`, disappeared once, and produced
the vendor's exact 50-gold quote. No `Use`, craft response, `PickUp`, `Drop`, or
bank action occurred; every transaction action was owned by the factory's exact
`CapabilityBoundSkill(SellDaggers)`. All 16 flags passed in 12 Agent ticks.

The production village CLI was then run with a fresh account prefix, one
miner+blacksmith pair, `--capability-goals`, and the real chronicle. It recorded
`sold_to_vendor` for 50 gold at tick 16 and `banked_gold` for 1150 gold at tick
45, proving the selector composed the two separately leased operations.

B5 still provisions its first daggers. The next autonomy step is a separately
evidenced crafting capability, followed by inventory/tool acquisition and a
durable repeat policy, so the smith can replenish sale inventory instead of
performing only the staged first cycle.

### B6 — verified dagger crafting + sale→bank→craft composition ✅

The closed registry now adds `("blacksmith", "craft_daggers")` after sale and
bank. Readiness requires the exact configured work tile, an owned backpack,
an owned smith tool, enough owned iron to reach five pack daggers, an idle
market/craft FSM, and no open UI or target. The leaf never inherits the legacy
blacksmith's ground-ingot pickup or walking powers: it may only `Use` the owned
tool and answer observed ServUO craft-gump replies for resource selection,
iron, bladed weapons, dagger, make-last, and terminal close.

Every response is paired with the live gump that exposes that exact structured
reply button. This prevents a stale root gump from consuming the iron-selection
transition. Completion is scoped to the active `goal_id` and requires the
recorded start inventory, exact new dagger serials, five-pack final inventory,
successful 3-ingot consumption per dagger, separately observed 3-ingot failed
attempts, total ingot delta, original stand position, and a closed/settled UI.
Malformed mixed inventory deltas abort and drain the gump. Step/attempt limits,
missing terminal gumps, and cancel/deadline paths all reach a bounded safe yield.

Offline: 1002 tests pass; Ruff, compile, and diff checks are clean. The isolated
B6 live gate passed twice on fresh accounts. In each run, Blacksmithing 50 made
the fixture deterministic: exactly 15 owned iron became five unique dagger
serials along `(15,0) → (12,1) → (9,2) → (6,3) → (3,4) → (0,5)`. The exact ten
actions were `Use → 7 → 6 → 22 → 16 → 21×4 → 0`; all 16 flags passed, every
action belonged to the registry's `CapabilityBoundSkill(CraftDaggers)`, the
sealed 300-tick COGNITION frame succeeded once, and no goal replay occurred.

The production village was then rerun on the final code with a fresh
miner+blacksmith pair, `--capability-goals`, and Chronicle. At the normal
Blacksmithing level, failed crafts consumed iron and were attributed
separately. The real ledger recorded `sold_to_vendor` 50 at tick 16,
`banked_gold` 1150 at tick 45, miner delivery of 12 ingots at tick 55,
`crafted_daggers` 4 at tick 81, and a second `sold_to_vendor` 40 at tick 100.
The smith retained one base dagger, created the four missing items to restore
the five-item batch, then sold that replenished inventory in the next cycle.

B6 closes first-cycle inventory replenishment, but not indefinite autonomy.
At this point `bank_gold` was still an absolute one-shot policy once the bank
already held 100 gold, and iron/tool supply remained finite.

### B7 — repeatable goal-scoped banking ✅

`blacksmith/bank_gold` no longer treats an absolute bank balance as completion.
Each admitted goal freezes the configured route and every owned backpack gold
pile, then copies the bank-box baseline only after the real Bank popup and
settle barrier have synchronized its existing contents. The leaf records each
exact `PickUp` and matching `Drop` into the character's own bank box. Success
requires the same goal id, the complete starting manifest, equal exact pack
decrease and bank increase, every source pile cleared, no held/recovery state,
and return to the original safe stand. A stale prior goal or an already-large
bank therefore contributes zero progress to a new deposit.

Readiness now accepts any positive pack gold. This is intentional: a normal
five-dagger sale pays only 40–50 gold, so retaining the old 100-gold admission
threshold would make the economy wait through several otherwise complete sale
cycles. The legacy `BlacksmithMarket` threshold remains unchanged. Multi-stack
banking also resets its bounded retry allowance after each newly confirmed
stack, preventing a successful early pile from exhausting later piles' budget.

Chronicle derives one terminal `(goal_id, confirmed_amount)` token from the
same baseline/action/delta evidence. Partial deposits that genuinely reached
the bank may still be recorded as economic facts even though the capability
does not succeed, malformed capability evidence cannot fall back to a legacy
reward, and a completed token remains valid through later sibling market
phases so it cannot replay when a sale returns to `craft`.

Offline: 1035 tests pass; Ruff, compile, and diff checks are clean. The isolated
repeat gate first established `pack=100, bank=100`, then production
`ThreadedCognition(CapabilityCognition)` created one sealed goal whose exact
actions were `PopupRequest → PopupSelect(Bank) → PickUp(100) → Drop(100)`.
All 16 flags passed twice, ending at `pack=0, bank=200` with exact goal-scoped
proof and one SUCCESS frame. The original B4 bank gate also retained all 16
flags.

A fresh 180-tick miner+blacksmith production run then recorded exactly:
`sold_to_vendor 50` (tick 16), `banked_gold 1150` (45), `crafted_daggers 4`
(77), `sold_to_vendor 40` (100), `banked_gold 40` (127), and
`crafted_daggers 4` (162). This proves a second sale's smaller proceeds became
a distinct bank goal over an existing balance and the loop continued. The run
also exposed and closed a Chronicle replay bug before this milestone was
accepted.

B7 closes repeat banking, but iron and smith-tool supply remain finite. B8
should add separately verified acquisition/replacement capabilities and their
recovery policy without broadening model-selected execution authority.
