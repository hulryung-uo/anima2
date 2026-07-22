# anima2

> *Anima (Latin: soul)* — a real character living in Britannia.

An **autonomous AI agent that plays Ultima Online** — the **Brain** that drives a
body. A clean redesign of [`anima`](https://github.com/hulryung-uo/anima) (v1), built on top of
[`anima-core`](https://github.com/hulryung-uo/anima-client) (the new Rust headless UO client).

> **New here? Read [`docs/DESIGN.md`](docs/DESIGN.md)** — the full design & handoff
> doc (what anima2 is, why, architecture, roadmap, what to reuse from v1). This
> project is resumable from that doc alone.

## The idea

anima2 perceives UO through a structured **Observation/Action contract** (never
pixels, never raw packets), decides with a **hierarchy of deterministic skills +
planner + LLM cognition**, remembers, talks in character, and **improves** by
accumulating skills and following a curriculum. It is the *driver*; `anima-core`
is the *car*.

```
   Director / Curriculum   (what to learn next)
            │
   anima2  BRAIN  ── Observation/Action ──▶  anima-core  BODY (Rust)
   reflexes · planner · skills · LLM · memory · persona
```

- **Fast loop (~100–250ms):** perceive → reflexes → planner → skill → act. No LLM. Always alive.
- **Slow loop (seconds–min, async):** LLM sets goals, handles social/novelty, reflects, proposes new skills. Steers; never blocks.

## Status

**Autonomy track begun:** the roadmap has been re-centered on turning the
staged worker into a self-sustaining UO player; see
[`docs/AUTONOMY-ROADMAP.md`](docs/AUTONOMY-ROADMAP.md). The A1–A4 survival and continuity
vertical is live-verified: the agent retreats and bandages wounds, cures poison,
quarantines ordinary work while dead, accepts only a verified free resurrection,
discovers a healer from server waypoints without a staged coordinate, recovers
its uniquely attributed corpse, and resumes the same Goal after death or an
abrupt IPC bridge restart. B1 is also live-verified: nested goals preserve exact
parent identity and observed progress across success and deadline expiry, while
stale or adversarial cognition cannot overwrite the active stack. B2 now turns
trusted curriculum milestones into exact, profession-bound work goals behind a
separate `--curriculum-goals` opt-in; arbitrary/cross-profession proposals fail
closed and work FSMs only yield at safe observation-confirmed boundaries. B3
adds a separate immutable capability registry and a sealed, deadline-bounded
`blacksmith/bank_gold` Goal whose exact shipped adapter can bank but cannot
craft, sell, or load a model-named skill. B4 connects that boundary to real
cognition: an opt-in selector may emit only strict JSON for `idle` or an
observation-ready opaque capability id; Agent still rechecks and seals it.
B5 adds a separately leased `blacksmith/sell_daggers` operation. Its success
requires goal-scoped proof of the exact vendor offer, dagger removal, quoted
gold arrival, and safe return before the selector proceeds to `bank_gold`.
B6 adds `blacksmith/craft_daggers`: it can use only the owned backpack hammer
and iron, verifies every live craft-gump reply, attributes successful and
failed ingot consumption to the active goal, closes the UI, and replenishes the
pack to one five-dagger sale batch. B7 makes `bank_gold` repeatable: each goal
freezes the exact pack piles and the settled bank baseline, owns the matching
pickup/drop actions, and succeeds only after equal pack and bank deltas plus a
safe return. The production village now repeats sale → bank → craft instead of
stopping after the first bank balance, and Chronicle records each goal once.
B8 adds `blacksmith/buy_ingots` — the self-provisioning keystone: the sell side
inverted (gold leaves, iron ingots arrive), so the loop replenishes its own
finite crafting metal with earned gold instead of stalling for a GM to re-gift
ingots. It buys only iron (never the vendor's other stock), spends exactly the
live-quoted price, and is ready only when iron is below one sale batch and the
gold is there to afford it. This required making the body contract's BUY window
carry per-item `serial/graphic/amount` (symmetric with the SELL window, so the
brain matches an offer by graphic and buys by serial); the contract advanced to
schema 16 (additive ClassicUO coverage) and the brain moved in lockstep. Its
tool-replacement sibling `buy_smith_tool` closes the loop's last GM dependency —
it buys one replacement tongs (via the same graphic-parametrized resolver) when
the smith's hammer wears out, so both finite crafting inputs (iron and the tool)
now replenish through normal vendor play.

**Phase 6 (the living village) — complete, all six items live-verified.**
**Phase 7 item 1 (profession-conditional pool routing + fishing `nodes_pool`
threading) — live-verified.** **Autonomy B8 (verified iron + tool acquisition) —
live-verified.** 1106 tests green, ruff clean. The Python
brain drives **live ServUO characters** through the `anima-agent` IPC bridge, from
a single agent up to a working **village** of profession-holding agents. Every
milestone below is verified against a real ServUO shard with a non-vacuous live
gate — differential where applicable, provenance-aware, cross-process-read — not
just an offline test.

| Phase | What landed | Verified |
|-------|-------------|----------|
| **2** — cognition + memory | Observation/Action contract, episodic memory, reflection loop, wiki semantic memory | ✅ closed out |
| **3** — economy & interaction | Miner→blacksmith ingot trade, sell-to-vendor + bank the gold, hunt/loot corpses, A\* navigation | ✅ all 4 items |
| **4** — the learning stack | Wiki write loop (LLM-judged discrepancy reports), cognition cost tiering, skill library, UCB1 bandit tuning, automatic curriculum | ✅ all 5 items |
| **5** — measurement & evolution | Independent "agents can't lie" fitness oracle, repeatable eval harness, MAP-Elites archive, config-space evolution loop | ✅ all 4 items |
| **6** — the living village | Persistent lives (insights survive the session), inter-agent relationship chronicle, forum as continuing chronicle, richer eval scenarios (fisher + cognition-aware), and the decisive evolution-vs-random rerun (honest result: random won at this budget) | ✅ all 6 items |

A few of the live proofs, to give the flavor of the verification culture:

- **The economy loop closes into gold, end to end.** A miner mines, smelts, and
  hauls ingots to a co-located blacksmith that has run its own stock dry; the
  blacksmith picks them up, crafts, sells the surplus daggers to a vendor
  (right-click context menu → `SellItems`), and banks the proceeds — every gold
  piece provably a sale (starting gold GM-deleted). See `live_trade.py` /
  `live_market.py`.
- **A\* navigation, proven differentially.** `GoTo` delegates to the bridge's
  route driver; a forced-greedy control run wedges on a rock-blocked Minoc-ridge
  course a straight line can't cross, while the real `GoTo` crosses it both ways
  (round trip). See `live_navigate.py`.
- **Evolution is measured, not asserted.** A config-space MAP-Elites loop runs
  against a random-search baseline on an identical budget, scored by an
  independent GM-read fitness oracle the agent's own code can never write — and a
  tie is reported honestly as a tie, not dressed up as a win.
- **The village remembers.** Reflection insights persist to disk so a brand-new
  process resumes a persona's inner life before its first tick; a chronicle
  ledger records real inter-agent events (who delivered to whom), cross-checked
  against an independent episode-transcript oracle; and forum posts are grounded
  in those real events (a qwen-written entry named its blacksmith partner by
  exact persona name, from a real confirmed delivery).

See [`docs/PHASE6.md`](docs/PHASE6.md) for the current work breakdown and
[`docs/DESIGN.md`](docs/DESIGN.md) §10 for the full roadmap.

## Run it

```bash
uv venv && uv pip install -e ".[dev]"
pytest -q                       # 1106 passing (offline; uses MockBody + a fake bridge)
python -m anima2                # offline demo: a miner walks to work, then wanders

# Live (needs a running UO server + the built bridge):
( cd ../anima-client && cargo build -p anima-net )
python -m anima2.live 127.0.0.1 2594 animatest animatest --goto 3720 2216
#   add --llm to use Claude cognition (needs ANTHROPIC_API_KEY + pip install -e ".[llm]")

# A working village (Control-plane staged; defaults: 2 miners, 1 each of the rest,
#   60 ticks) — a roster with both a miner and a blacksmith co-locates the first
#   of each at a calibrated trade spot, wires up ingot delivery, and stages a
#   vendor + banker so the paired blacksmith can sell/bank too:
python -m anima2.village
#   add --curriculum-goals to drive profession work from admitted catalog Goals
#   add --capability-goals to let the paired blacksmith select verified operations
#   add --account-prefix freshname for an isolated first-run village/account set
#   add --chatter for LLM in-character speech + goal:goto (needs a Replicate key in
#     anima v1's config.yaml, or REPLICATE_API_TOKEN — no extra pip install)
#   add --forum to post each villager's day to uotavern (needs ANIMA_FORUM_API_KEY,
#     or the forum key in anima v1's config.yaml)
#   add --hunters N to include the hunter profession (opt-in, default 0)
#   add --llm-tiers {anthropic,replicate,stub} for role-tiered cognition (chatter +
#     reflection) via build_tiered_clients — supersedes --chatter when both are given

# Single-skill live proofs (GM stages the scenario, then the brain works it):
python -m anima2.live_mine      # mines ore, Mining skill rises
python -m anima2.live_smelt     # mines then smelts ore into ingots, end to end
python -m anima2.live_reflect   # LLM cognition + reflection, wiki-grounded prompts
python -m anima2.live_trade     # 2-agent inter-agent economy proof: miner -> blacksmith
python -m anima2.live_market    # blacksmith sells daggers to a vendor, banks the gold
python -m anima2.live_hunt      # bare-handed hunter kills weak creatures, loots corpses
python -m anima2.live_navigate  # differential proof: greedy wedges, WalkTo-delegated GoTo crosses (round trip)
python -m anima2.live_survival  # A1: flee, self-bandage, and observed HP recovery
python -m anima2.live_recovery  # A2: poison cure + death/resurrection/corpse/Goal continuity
python -m anima2.live_reconnect # A3: kill the live bridge, reconnect, and resume the same GoTo
python -m anima2.live_waypoint_recovery # A4: discover healer E5, resurrect, recover corpse, resume Goal
python -m anima2.live_goal_stack # B1: interrupt, deadline, cognition isolation, resume same Goal
python -m anima2.live_bank_goal  # B3: invalid-goal differential + exact 100-gold bank transaction
python -m anima2.live_repeat_bank_goal # B7: second deposit over an existing bank balance
python -m anima2.live_buy_goal    # B8: buy iron from a vendor — exact quoted spend, iron arrives, only iron
python -m anima2.live_toolbuy_goal # B8: buy a replacement smith tool (tongs) when the smith holds none
```

## Family

| Project | Role |
|---------|------|
| [`anima-core`](https://github.com/hulryung-uo/anima-client/tree/main/crates/anima-core) | Body — UO protocol, world, assets, path (Rust, headless) |
| [`anima-client`](https://github.com/hulryung-uo/anima-client) | Cross-platform client wrapping anima-core (+ web renderer) |
| [`anima`](https://github.com/hulryung-uo/anima) (v1) | Original Python AI player + Foundry evolution (mined for assets/lessons) |
| **anima2** | **Brain** — this project |
