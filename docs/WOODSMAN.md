# Bjorn — a lumberjack's life

The third profession to live autonomously (after the swordsman and the mage), and the
first that does not fight for a living.

```
python -m anima2.village --woodsman [--monitor]
```

## Why this profession was worth building

Not for variety. The first two lives stop working for reasons that are structurally the
same — something they carry runs out or is lost, and the fix is one purchase. A woodsman
differs in two ways that exercise parts of the system the others never touched.

**Its chain is longer.** A warrior turns a fight straight into gold. A woodsman has to
carry its value through `tree -> log -> board -> gold`, so the rule has to prefer the
link that moves material *furthest along* rather than the first one that is ready:

```
axe (lost or broken)  >  sell boards  >  process logs  >  bank  >  chop
```

Selling above processing is the ordering lesson the artisan's chain-priority client had
to learn live: an agent that always takes the first ready step keeps making more of what
it already has and never finishes anything.

**Its tool is consumable.** A blade is lost to a specific event; a reagent pouch empties
predictably; an axe just breaks mid-swing. The rule prefers an axe lying on the ground
(free — and the shape a tinker's `deliver_hatchet` produces) over buying one, and a
woodsman that can do neither keeps chopping rather than stalling at a shop it cannot use.

What is *not* handled here is the other half of that difference: the world itself runs
out. A grove thins until swinging at it earns nothing, and that is left to `Harvest`'s
own windowed stuck-rate relocation, not to this rule.

## What the first day cost — two defects, both invisible from outside

Bjorn chopped 20 logs and then did nothing for an entire run, twice, with the
orchestrator selecting `process_logs` correctly every tick.

### 1. Three skills reached for somebody else's tool

`ProcessLogs._axe`, `Harvest._tool` and `Blacksmith._tool` scanned every item in view by
graphic with no owner filter — and the staged Weaponsmith standing one tile away *wears
and sells axes*. `Use` on a stranger's tool does nothing.

`harvest.owned_tool` now backs all three, written as a **refusal** rather than an
allow-list: reject a tool held by another mobile or inside another mobile's backpack,
allow everything else. The first attempt was an allow-list (ours / worn / loose) and it
mis-rejected tools whose owner could not be confirmed — e.g. with our own backpack out of
view — breaking 16 tests that described legitimate behaviour.

The knowledge was already in the file: `Harvest._backpack`, immediately above the finder
that had the bug, carries a long docstring about why picking items by shape rather than
owner returns a neighbour's. It was never applied to the tool finders beside it.

### 2. A worn tool did not count as a tool

This was the one that actually stalled the run, and it had no outward signature at all
until the telemetry to see it was built:

```
want=process_logs  admitted=None  ready=[]  [axe_in_pack=False worn=True]
```

A lumberjack, of course, **wields its axe**. Every part was individually right:

| component | verdict on a worn axe |
|---|---|
| `ProcessLogs._axe` (the skill that swings it) | I have an axe |
| `WoodsmanLife._has_axe` (the rule that decides) | so process the logs |
| ServUO `BaseAxe.OnDoubleClick` | reach + accessibility, no backpack required |
| **`capabilities._owned_tool` (the gate)** | **no tool** |

So no goal was ever admitted, and the capability leaf sat returning `RUNNING` with
nothing to do — which from outside is indistinguishable from working.

The direction of the fix was **measured, not chosen**: a raw conversion probe with the
axe packed converts 20 logs in two ticks, and a second probe with the axe *equipped*
(`container` = the player, layer 2) does exactly the same. The gate was the odd one out,
so it was widened. Its old behaviour had a quieter cost too — a worn tool read as no
tool, so a `buy_tool` trigger could spend gold on one the agent was already holding.

The regression pins the **agreement** between gate and skill, not either side's answer:
the defect was a disagreement, so that is the property that must not drift again.

## Telemetry that earned its place

`ready=[]` says the gate refused. It does not say *which condition* did, and the
candidates differ in kind — a tool the gate cannot see, a cursor another skill left open,
a market phase never cleared. So the runner prints all three layers separately:

```
want=<what the orchestrator wants>  admitted=<the goal actually admitted>  ready=<what the gate allows>
[axe_in_pack=? worn=? cursor=? mkt=?]
```

`want` alone is a trap: it is *intent*, and an unadmitted goal looks identical to a busy
one. The `axe=` readout is owner-filtered for the same reason — reporting "yes" for the
Weaponsmith's axe is exactly how that display would have hidden the first bug.

## Live result

With both fixed, the chain turns unattended and repeats:

```
admitted=process_logs  ready=['process_logs']         logs 20 -> 0, boards 0 -> 20
admitted=sell_boards                                  boards 20 -> 0
admitted=None          ready=['sell_boards','bank_gold']
gold: 0 -> 40 -> 80 ...
```

Gold is deleted at staging, so every coin is one Bjorn earned selling boards.

Staging follows the village's own hard rule: the three shops (Carpenter for boards,
Weaponsmith for axes, Banker) are placed inside the market skill's reach of the grove
stand — so the trip short-circuits `_walk_route`'s final-reach check and needs no greedy
crossing — and then **read back** from the server with their real distance printed, since
an `[Add`-ed NPC settles a tile or two off the request.
