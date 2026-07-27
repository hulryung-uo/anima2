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
