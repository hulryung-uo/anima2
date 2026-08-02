# Sten — a carpenter's life, and why it cannot be lived alone

The fourth profession to live autonomously (after the swordsman, the mage and the
woodsman).

```
python -m anima2.village --carpenter [--monitor]
```

## Two things make this one structurally different

**It has no work skill.** `PROFESSIONS["carpenter"].work_skill is None`. The other three
always have something free to fall back on — a warrior swings at what is in front of it,
a mage casts while its pouch holds ash, a woodsman walks to a tree and chops — each a
plain skill running outside the capability system, so "work mode" always means
something. For a carpenter, crafting, selling, restocking, replacing its saw and banking
are *all* goal-scoped capabilities. So its mode rule answers with a capability
essentially always, and the run is really a test of whether the capability layer alone
can carry an agent through a day. It can: buy → craft → sell completed on the first
attempt.

**Its blocker is somebody else.** A woodsman makes its material out of the world; a
carpenter cannot. It either buys boards or picks up boards a lumberjack delivered — which
is why `fetch_boards` is preferred over `buy_boards` even when it can afford them.

## The economics — measured, then confirmed at the source

That preference turned out not to be a nicety. From ServUO's own price table
(`Scripts/VendorInfo/SBCarpenter.cs`):

```
Board    buy 3g / sell 2g
Throne   sell 24g
```

and `CarpenterCraft` consumes **19 boards per item**:

| material source | per throne |
|---|---|
| bought (19 × 3g = 57g in, 24g out) | **−33g** |
| delivered (0g in, 24g out) | **+24g** |

Selling the boards back raw (19 × 2g = 38g) beats crafting them into a throne. At vendor
prices the craft **destroys value**, so a carpenter buying its own material cannot
profit, however well the code works.

The live solo run shows exactly that, and shows the rule behaving correctly while it
happens:

```
gold 129 (seed) -> 69 (bought 20 boards) -> 93 (sold one throne) -> stops
buy x1 · craft x1 · sell x1 · bank x0 · banked=0
then 93 samples of waiting
```

It is not stuck — 880 steps, alive and moving. It is *waiting*, correctly: one board
left (a recipe needs 19), 93 gold against a 114-gold restock, and the same rule all four
lives share says do not stand at a shop you cannot pay for.

**So the village is not flavour.** Specialists supplying each other is an economic
requirement for this profession, and that is something the solo run proved in numbers
rather than something the design asserted.

### Measured a second time (2026-08-03), and what the repeat adds

`python -m anima2.village --carpenter --knob bank_reserve=400 --ticks 300 --monitor`,
local ServUO, 32 telemetry samples. It reproduces the block above figure for figure —
129g seed, `129 → 69` (20 boards), `69 → 93` (one throne), boards=1, then a flat tail
(30 samples here against the first run's 93, because the budget was 300 ticks and not
1200; 277 steps, alive, hp 80 → 48). A price-table derivation that survives two
independent live runs unchanged is worth more than either run alone, so the warning above
is now measured rather than argued, twice. Three things the repeat adds:

**The realised margin is −36g, not −33g, and the difference is the vendor's shelf.** The
table's −33g assumes the 19 boards a throne consumes. The runner does not buy 19: it buys
a batch, and `BuyMaterialCapability` orders `min(self.buy_amount, entry.amount)`
(`skills/market.py:852`). `BuyBoards.buy_amount` is **38**, so the 20 boards both runs
came home with is the SHARD's shop entry, not the brain's choice — one board is stranded
at 3g and the realised per-throne margin is −36g. When re-running this, expect the
vendor's stock to set that number.

**It ended below the price of its own next attempt, and that is a one-cycle property.**
`decide_mode` will not walk to the vendor without `BOARD_BATCH_COST` = 38 × 3g = **114g**
in the pack — correctly, since standing at a shop you cannot pay for is the failure that
rule exists to prevent. The 129g seed funds exactly one attempt; a throne returns 24g;
93 < 114. So a carpenter buying its own material does not merely fail to profit, it
prices itself out of the market in a single cycle, with a board and a saw and nowhere to
go. That is the sharpest statement of why this chain is a mechanism proof and not an
economy.

**The waiting was correct; the report of it was not.** The first run's record reads "it is
not stuck — it is *waiting*, correctly", and that still holds — the rule's `return "hunt",
None` on "no material and no means" is the right answer at 93 gold. What the second run
saw, because it was watched with `want=`/`admitted=`, is that for all 272 ticks of that
wait the status line said `admitted=sell_furniture`: the sale's goal frame was still on
the economy agent's stack, stranded mid-transaction because leaving economy mode is
exactly what stops that agent being ticked. Nothing was lost — the sale had completed and
the gold was in the pack — but an operator reading that line would have believed a sale
was in flight for the entire tail. Full evidence and mechanism:
`docs/AUDIT-2026-07-29.md`, the 2026-08-03 entry §3.

**That is FIXED, and this doc is where the fixed behaviour has to be read back, because
the frozen tail above is the thing a future reader would otherwise take for normal.**
`WarriorLife.tick` now HOLDS the economy mode while a goal frame is live, so the sale's
own agent keeps being ticked and the frame finishes and retires instead of freezing —
offline, this exact carpenter now closes it as `('sell_furniture', 'failure')` on the
FSM's own give-up ladder at 17 economy ticks, and only then falls back to waiting. What
the tail looks like now, printed by the same `telemetry_line` the live run used, on the
same fixture:

```
[economy] want=None admitted=sell_furniture@5/180+hold  ready=[]
[economy] want=None admitted=sell_furniture@6/180+hold  ready=[]
   ... the age CLIMBS, eleven more ticks ...
[economy] want=None admitted=sell_furniture@16/180+hold ready=[]
[hunt   ] want=None admitted=None                       ready=[]
```

`want=None` with an admitted goal is no longer the symptom — `+hold` says the orchestrator
is deliberately finishing an owed transaction. The symptom is now an `@N` that STOPS
climbing while the samples keep arriving.
The fix, its three bounds and its test coverage are in the audit's 2026-08-03 §5;
**it is proven OFFLINE ONLY — no shard has run it**, so the first live carpenter after
this is also the fix's first live exposure.
See `docs/WOODSMAN.md`'s telemetry legend for what `@age/budget`, `+hold`, `!frozen` and
`!overdue` mean on a status line.

(The `--knob bank_reserve=400` in that command line proved the tuning channel live — the
staging banner printed `(reserve 400)`, not the 129 default — but it changed nothing in
this run: at 93 gold the bank branch is out of reach at either value.)

## Lessons wired in from the start

Everything the previous three lives paid for was applied before the first run rather than
rediscovered:

- the reserve is **derived** (one board restock + a spare saw = 129g), and written to
  `bank_reserve` so the rule, the `bank_gold` gate and `BankGold`'s FSM keep the same
  amount — the woodsman learned that one late;
- the rule compares `>` to match the gate's own comparison exactly;
- a **worn** saw counts as owned (`capabilities._owned_tool`, widened after a worn axe
  cost a woodsman an entire run);
- shops are **read back** after staging with their real distance printed, and a shared
  tile is reported outright.

## The one thing that still had to be caught live

Provenance. The first run had the chain working immediately — and its numbers lied. A
fresh ServUO account arrives with ~1000 gold, Sten promptly banked it, and the readout
reported **940 "earned"**. The woodsman deletes starter gold for exactly this reason; the
carpenter did not.

Starter gold is now deleted *before* the seed is added, and the seed is exactly one board
batch plus a saw — enough that a profession which cannot make its own material is not
stuck at tick zero, and nothing beyond that is a gift. Every coin after that is earned.

Worth noting what the uncaught version would have produced: a headline result of "the
carpenter banked 940 gold", which is both impressive and false.

## The supply pair — done, and what it took

`python -m anima2.village --supply-pair` runs Bjorn hauling boards to Sten's drop point.
Live, over 1200 ticks:

```
Bjorn  chop -> process -> deliver (40 boards on the ground, BOTH agents seeing them)
Sten   fetch -> craft (2 thrones) -> sell
Sten's gold: 15 (saw seed) -> 39 -> 63
```

Two independent agents, separate memories, no messages between them — one leaves
material on the ground and the other finds it. Sten's 48 earned gold all came out of
Bjorn's trees. It ends with Sten correctly WAITING (2 boards against a 19-board recipe,
63 gold against a 114 restock) rather than standing at a shop he cannot pay for.

It took four attempts, and each failure was a variation on one theme — a component that
was individually right, disagreeing with its neighbours:

1. **the handover tile was never checked.** The carpenter's vendor settled onto the drop
   itself. The collision check compared shops against SHOPS and never looked at the tile
   the boards had to land on.
2. **the pair runner shipped without the want/admitted/ready telemetry**, so Bjorn
   sitting on 20 logs for 123 samples said nothing about why.
3. **the fetch rule ignored distance** while its gate requires `PICKUP_RADIUS`, so a
   carpenter nine tiles away kept wanting a pickup admission had to refuse.
4. **the leash bound only one agent.** Each life runs two agents with separate memories
   and both planners end in `Wander`, which reads whichever is ticking. A carpenter has
   no work skill, so it runs the ECONOMY agent nearly every tick — and that one was
   never leashed. It drifted off the corridor and walked into a wall with
   `fetch_boards` correctly wanted, admitted and ready the whole time.

(4) was the decisive one, and probing the ground first is what made it findable: the
delivery corridor (518,1043)-(518,1045) walks fine both ways and everything west of the
stand is blocked, so the drop and the layout were right. Without the probe the obvious
move would have been relocating a drop that had nothing wrong with it.

**The economics do not change.** At vendor prices the village is still richer if Bjorn
sells his boards, so supplying stays an explicit choice — `WoodsmanLife` only prefers a
partner when `carpenter_drop` is configured, and an unpartnered woodsman is unaffected.
What the pair proves is the mechanism, which is what pays the moment crafted goods are
worth more than vendor scrap.
