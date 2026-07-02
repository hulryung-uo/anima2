---
title: Mining
description: Digging ore and stone from mountains — ore banks, MaxRange, and smelting at a forge.
status: source-verified
sources:
  - "servuo: Scripts/Services/Harvest/Mining.cs"
last_verified: 2026-06-11
---

<img src="/img/skill-flags/45.gif" alt="Mining skill banner" width="160" />

Swing a pickaxe at a mountainside and Britannia pays you in ore. Mining is the
realm's foundational gathering skill.

## How it works

Resources come in **8x8-tile banks** (`BankWidth = 8`). You can dig tiles up to
**2 tiles away** (`MaxRange = 2`); each success yields ore. Smelt ore at a
[forge](/items/forge/) into ingots.

## Training

Dig ore endlessly to raise Mining skill — a pure resource loop.

## Where

[Minoc](/world/minoc/) is the miner's town: mountains, forge, and bank in a
tight loop.
