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

## Next

The obvious continuation is the supply pair. `WoodsmanLife` already has `deliver_boards`
in its capability set, and `CarpenterLife` already prefers `fetch_boards` — Bjorn hauling
boards to Sten's drop point is the case both were built for, and the only arrangement in
which a carpenter turns a profit.
