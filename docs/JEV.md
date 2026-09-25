# Jev in anima2 — where a System One judge fits, and where it does not

Jev (TypeSafe, `POST /v1/systemone`) answers typed questions about a state, and never writes text:
**Choice** (one of named options plus the distribution), **Noul** (P(yes)) and **Score** (a position
on a graded scale). One call can ask several questions about the same state. On the probes in
`~/dev/jev` (jev-testbed) it scored 14/14 on "did this line break character" and 3/3 on
numeric state, where the open imitations (jeff/GLiFormer, Laya) scored 6/14 and 1/3. It costs
about $0.017 per thousand calls, with a median of 230–350 ms (p90 about 560 ms).

anima3 measured where it does **not** belong: a head choosing every action loses tempo against
a near-optimal rule. Over 27 matches of a 5x mage duel, Qwen deciding every cast won 30.9% of
rounds against the rule (p < 0.001, also at the match level). Jev was level with the rule, and a
2–3 s API window lost every round. So the model is asked at **boundaries**, meaning a mode
switch, an encounter or a heard line, and the rule executes.

## What anima2 has today (2026-09-25)

- **Steering, `warrior_life.py` `_LifeClient._work`:** sends Haiku only the candidate *names*
  ("buy_reagent, fetch_gold, bank_gold"), with no state. The testbed reproduced this: with no
  state, every model is blind (Jev confidence 0.30). With the state rendered as sentences, Jev
  matched the rule in 3 of 4 scenarios. Only `MageLife` sets `decide_all`.
- **Choose-one tasks done by generating text:** `capability_pick` (`capability_cognition.py:102`)
  and `curriculum_pick` (`curriculum.py:738`) ask Sonnet for JSON and then parse it.
- **Out-of-character checks:** substring blocklists (`cognition.py:71`, `forum.py:209`).
- **Chat:** no classifier decides whether to answer a heard line.
- **Logs:** decisions are not logged together with the state they saw
  (`llm_usage.jsonl` has no prompt or answer).

## Plan

| # | Change | Where | Question | Why first |
|---|---|---|---|---|
| 0 | Bridge schema 32 (done) | `ipc_body.py` | — | `mobiles[]` now carries `poisoned`, `paralyzed`, `war_mode`, `hidden`, `running` and `direction` |
| 1 | A typed `Judge` protocol next to `LLMClient` (port `anima3/judge.py`: `JevJudge`, `StubJudge`, an off-thread `Asker` that logs state + answers as JSONL) | new `judge.py` | — | Everything below needs it; the log closes the dataset gap |
| 2 | Out-of-character screen → Noul | `cognition.py:71`, `forum.py:209` | "Does this line break character by speaking as an AI assistant?" | Clearest measured win (14/14 against the keyword matcher's false hits on "I cannot afford…"); one call per generated line |
| 3 | Steering with state → Choice | `_LifeClient._work` | the candidates' `description` as criteria; the state rendered as **sentences**, not JSON (JSON made Jev waver in the testbed) | Today's steering is provably blind; keep the 5 s deadline, the `cands[0]` fallback and add a confidence gate (0.35) |
| 4 | `capability_pick`, `curriculum_pick` → Choice | `capability_cognition.py`, `curriculum.py` | ready ids / eligible milestones as criteria | Replaces a Sonnet JSON call (seconds, parse failures) with ~300 ms typed output |
| 5 | A heard-line gate → Choice + Noul in one call | new, before `LLMCognition` | kind (greeting/question/trade/threat/chatter) and "is it addressed to this character and worth an answer?" | There is no gate today; the LLM speaks on a timer instead of in reply |
| 6 | Encounter judgement → Score + Noul | `combat.py` / `WarriorHunt.can_run` at first sight of a mobile | threat 0–2 from name, notoriety, condition and our HP/gear; "worth engaging?" | Nearest-target plus notoriety-only is the weakest rule; asked once per encounter, never per tick |

Not planned: per-tick action choice, heal and flee thresholds inside a fight. Those are
reflexes, and the rule is fast and near-optimal there.

## How to measure each step

Every judged boundary is logged as (state, questions, answers, ms), and the rule's own choice
goes into the same row. For each step, run the Life/village with the rule and with the judge
on the same seed or config, and compare the outcome that step is meant to move:

- steering and capability picks: gold per hour and stock-outs;
- the screen: lines screened, sampled by hand;
- the chat gate: replies per addressed line;
- encounters: deaths and gold per engagement.

A step whose outcome does not move stays off.
