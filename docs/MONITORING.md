# Watching an agent play

`--monitor` serves a **read-only web view of each agent's own character**, on loopback:

```
$ python -m anima2.village --pipeline --monitor
  animat153595: the tinker  watch: http://127.0.0.1:8801/
  animam153595: the mage    watch: http://127.0.0.1:8802/
```

Open either URL and you see the real UO client — terrain, sprites, journal — rendered
from that agent's live session. `--monitor` works the same for `--warriors N`.

## Why it is not a second login

The obvious design — log a spectator in as the same character — cannot work. A UO shard
allows exactly **one session per character**, and ServUO enforces it by disposing the
older one (`Server/Network/PacketHandlers.cs`):

```csharp
if (m.NetState != null) { m.NetState.Dispose(); }
```

So a "monitor login" would kick the agent off its own body — the opposite of watching it.
The agent already holds the only session that character gets, so the viewer **attaches to
that session** instead of competing for it: the bridge renders frames from the session it
owns and serves them over HTTP.

Consequences worth knowing:

- **One viewer per agent.** Each agent runs its own bridge process and therefore its own
  port (`village.MONITOR_PORT_BASE`, then one per agent). There is no way to watch several
  characters through one connection, because there is no one connection.
- **The view survives a reconnect.** `ResilientIpcBody` passes the port to both of its
  spawn factories, so a restarted body comes back on the same URL.

## Read-only is structural

`PlayConfig::read_only` refuses `POST /input`, `/login` and `/character` with **403** in the
HTTP layer itself, before the request body is read — so a click can never reach the action
or login channels:

```
$ curl -X POST -d walk_north http://127.0.0.1:8801/input
read-only monitor: input is disabled          # HTTP 403
```

This matters beyond tidiness: the brain must stay the only thing driving the body, or a
live run stops being evidence of what the brain can do.

## What it costs

Rendering a frame happens on the bridge's own thread — **in the brain's critical path** —
so it is bounded twice:

- **only while watched**: no frame is built unless `/scene.json` was fetched in the last 5s,
  so an unwatched monitor costs one clock read per command;
- **at most every 250ms**, the same floor the human client uses.

The cost is *measured*, not assumed. The bridge reports it to stderr and warns past the same
30ms threshold the human client uses:

```
[anima-agent] monitor frames n=50 avg=…ms max=…ms
[anima-agent] slow monitor frame: …ms
```

If you are chasing a performance question, read those numbers before blaming (or clearing)
the monitor.

## When the game view is the wrong tool

The view shows *the world*. It does not show why an agent chose what it chose — its READY
set, its held goal, whether its worker is even still running. The village's own status line
carries that, and it is usually the faster answer:

```
artisan[tongs=5 gold=140] purse[mage_sees=0 artisan_sees=140 at=(2609,476) mage_is=1away]
mage[hp=86/90 gold=0 ash=16] artisan_ready=['craft_tongs'] artisan_goal=deliver_gold
```

A frozen agent is the case to watch for: if every counter (steps, reward, hp, position) is
identical across samples, the worker has **stopped**, and nothing read from its observation
after that point means anything — see `_run_worker`'s `DISCONNECTED` / `BUDGET SPENT`
markers, which exist because that ambiguity once cost three runs and a wrong root cause.

A frozen GOAL inside a running agent is the subtler cousin, and it cost a fourth run
(2026-08-03): the worker was fine, the character was walking, and `admitted=sell_furniture`
printed for 272 ticks with nothing executing it — the frame's owner agent had simply
stopped being ticked. `admitted=` now carries the frame's `@age/budget` in the owning
agent's own ticks, so a goal nobody is advancing is one whose age does not move while the
samples keep arriving, and three markers name the state outright: `+hold` (legitimate — the
orchestrator is finishing an owed transaction the rule stopped wanting), `!frozen` (nobody
is ticking this frame; if the character is not dead, that is a regression), `!overdue` (past
its own budget — the Life also prints a throttled `FRAME OVERDUE` line). Full legend:
`docs/WOODSMAN.md`. When that was written the orchestrator-side fix behind those markers was
proven **offline only** — `docs/AUDIT-2026-07-29.md`, 2026-08-03 §5 — so the next live run's
status line WAS the verification.

**Three live runs the same day ran it, and the verdict is split (audit §6).** LIVE-PROVEN:
the `@age/budget` clock on all 306 samples, and `+hold` on 31 of them in one 1800-tick
forge-pair run, with the frame's age advancing 1:1 and the frame retiring inside its budget;
the A/B that started this went from 30 lying lines out of 32 to **0 out of 33** on the same
command. STILL OFFLINE-ONLY at that point: `!overdue` and the throttled `FRAME OVERDUE` line
had **zero live ticks** — no frame went overdue — and `!frozen`'s clean sheet was entailed by
that rather than earned, since the telemetry can only print it when a death episode is open
or a frame is overdue-and-unrepaired.

**A fourth run the same day put the overdue state on a shard for the first time** — the
forced-state gate `anima2/live_frame_overdue_gate.py` (audit §7). A `craft_tongs` frame went
overdue at economy tick **301** against `deadline_tick=300`, `_repair_overdue_frame` closed
the craft FSM's own gump, and the hold released one tick later into `mode=hunt` with the
frame still live. So the overdue state itself is live-proven; `!frozen` on a live frame with
the character not dead is still the regression signal, and it is now a state a shard has
actually produced (as `!frozen!overdue` together, which is its legitimate form).

**Operational consequence for this doc, and it is the whole reason the gate recorded every
tick instead of sampling: do NOT read bound 3 off the `+hold`+`!overdue` pairing.** That
pairing exists for exactly ONE tick — the repair-and-extend tick — and `LifeRunner` samples
every 4.0 s (~9 ticks), so a sampling monitor misses it roughly 8 times in 9. The
sample-independent signals are the worker's own unthrottled prints: `FRAME OVERDUE` appears
on the FIRST overdue tick always (`_run_worker` prints on `_overdue % _QUIET_TICKS == 0`
starting at `_overdue == 0`), and `_repair_overdue_frame` is identified by a
`closing an unowned … ` line **immediately before** it with **no** `RULE-vs-GATE
DISAGREEMENT` line — the detector's copy of that repair always prints one alongside. Audit
§7.6 has the full reading key, including how to tell a bound-2 near-miss from a miss.

*A gap this monitoring doc should own, found by the same runs (audit §6.5, follow-up 17):*
`run_forge_pair`'s status line prints **no hp and no death flag for either agent**.
`life_runner.hp_readout` exists and returns `DEAD`, but the pair line never calls it, so
when the 1800-tick run's miner stopped producing at t=765 — reward and steps frozen, no
smelt and no deliver on the 126 samples that followed — nothing on the tape said so, and a
death, a lost tool and a dead vein remain indistinguishable. This is the second sighting of
the 2026-07-30 health check's "liveness line for NON-Life agents" item.

## Implementation

| Piece | Where |
|---|---|
| `read_only` flag, 403 enforcement | `anima-client` `crates/anima-net/src/play_server.rs` |
| `PlayServer::into_monitor()` → `Monitor` | same file — keeps the serving half, drops login |
| `Monitor::publish` / `watching` / `build_stats` | same file |
| `ANIMA_MONITOR_PORT`, publish per command | `crates/anima-net/src/bin/agent.rs` |
| `monitor_port=` passthrough | `anima2/ipc_body.py` |
| `--monitor`, port allocation | `anima2/village.py` (`_monitor_ports`) |

Two subtleties are load-bearing:

- **stdout is a protocol.** The bridge speaks NDJSON on stdout, so every `println!` in
  `play_server.rs` is an `eprintln!`. A single stray stdout line makes the brain drop the
  body with "bridge emitted malformed NDJSON" — which is exactly what happened the first
  time this was wired up.
- **the monitor keeps its own journal cursor.** `Session::observation()` advances the
  session's single shared cursor; rendering a frame for a spectator must never consume
  journal lines the brain has not seen yet.
