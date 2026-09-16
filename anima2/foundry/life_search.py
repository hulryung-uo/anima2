"""Budgeted, reproducible configuration search through the production Life factory.

The built-in evaluator measures an offline swordsman's bank transfer. It screens
configuration effects; it does not simulate combat or prove a profitable live policy.
Other Life scenarios can supply an evaluator without changing profile/admission rules.
"""

import argparse
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import random
from typing import Callable, Mapping

from ..life_config import LifeProfile, life_class
from .life_eval import LifeResult, LifeTrial, run_life_trial


@dataclass(frozen=True)
class SearchTrial:
    profile: LifeProfile
    result: LifeResult

    def as_dict(self):
        return {"profile": self.profile.as_dict(), "result": asdict(self.result)}


@dataclass(frozen=True)
class SearchResult:
    trials: tuple[SearchTrial, ...]
    best: LifeProfile | None
    axis_evidence: dict
    seed: int

    def as_dict(self):
        return {"schema": 1, "seed": self.seed,
                "best": self.best.as_dict() if self.best else None,
                "axis_evidence": self.axis_evidence,
                "trials": [trial.as_dict() for trial in self.trials]}


def _axis_evidence(trials, axes):
    evidence = {}
    for axis, values in axes.items():
        groups = defaultdict(dict)
        for trial in trials:
            knobs = trial.profile.knobs
            if not all(name in knobs for name in axes):
                continue
            context = tuple((name, knobs[name]) for name in axes if name != axis)
            groups[context][knobs[axis]] = trial.result.banked
        comparable = [group for group in groups.values() if len(group) > 1]
        varying = any(len(set(group.values())) > 1 for group in comparable)
        evidence[axis] = {
            "effect": ("varying" if varying else "flat_in_observed_pairs")
                      if comparable else "unmeasured",
            "compared_contexts": len(comparable),
            "complete_contexts": sum(set(values) <= set(group) for group in comparable),
        }
    return evidence


def search(base: LifeProfile, axes: Mapping[str, tuple[int, ...]], *, budget: int = 30,
           seed: int = 0, ticks: int = 400, gold: int = 2000,
           evaluator: Callable[[LifeProfile], LifeResult] | None = None) -> SearchResult:
    if type(budget) is not int or budget < 1:
        raise ValueError("search budget must be a positive integer")
    if type(ticks) is not int or ticks < 1 or type(gold) is not int or gold < 0:
        raise ValueError("ticks must be positive and gold non-negative integers")
    axes = {name: tuple(values) for name, values in axes.items()}
    if not axes or any(not values for values in axes.values()):
        raise ValueError("search needs non-empty axis domains")
    for name, values in axes.items():
        for value in values:
            LifeProfile(base.role, {**base.knobs, name: value})
    axes = {name: tuple(dict.fromkeys(values)) for name, values in axes.items()}
    if evaluator is None:
        if base.role != "swordsman":
            raise ValueError("the offline bank scenario supports only swordsman; supply an evaluator")
        cls = life_class(base.role)

        def evaluator(profile):
            return run_life_trial(LifeTrial(cls, profile.knobs, ticks=ticks, gold=gold))

    total = math.prod(len(values) for values in axes.values())
    if total > 10_000_000:
        raise ValueError("search grid exceeds 10 million configurations; narrow the domains")
    indices = random.Random(seed).sample(range(total), min(total, budget))
    profiles = [base]
    seen = {tuple(sorted(base.knobs.items()))}
    for index in indices:
        knobs = dict(base.knobs)
        for name, values in axes.items():
            index, digit = divmod(index, len(values))
            knobs[name] = values[digit]
        key = tuple(sorted(knobs.items()))
        if key not in seen and len(profiles) < budget:
            profiles.append(LifeProfile(base.role, knobs))
            seen.add(key)
    trials = tuple(SearchTrial(profile, evaluator(profile)) for profile in profiles)
    viable = [trial for trial in trials if trial.result.achieved > 0]
    best = max(viable, key=lambda trial: trial.result.banked).profile if viable else None
    return SearchResult(trials, best, _axis_evidence(trials, axes), seed)


def _axis(raw):
    name, separator, values = raw.partition("=")
    if not separator or not name:
        raise argparse.ArgumentTypeError("axis must be NAME=VALUE,VALUE,...")
    try:
        return name, tuple(int(value) for value in values.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("axis values must be integers") from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, help="optional Life profile JSON")
    parser.add_argument("--axis", type=_axis, action="append", required=True)
    parser.add_argument("--budget", type=int, default=30, help="maximum evaluations, including baseline")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--ticks", type=int, default=400)
    parser.add_argument("--gold", type=int, default=2000)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--profile-out", type=Path, help="export the best offline candidate for live checking")
    args = parser.parse_args()
    if len(dict(args.axis)) != len(args.axis):
        parser.error("each axis may be specified once")
    try:
        base = LifeProfile.load(args.base) if args.base else LifeProfile("swordsman", {})
        result = search(base, dict(args.axis), budget=args.budget, seed=args.seed,
                        ticks=args.ticks, gold=args.gold)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    report = result.as_dict()
    report.update(scenario="offline_bank_transfer", ticks=args.ticks, gold=args.gold,
                  limits="No combat damage, live prices, or autonomous income. Confirm candidates live.")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.profile_out and result.best is not None:
        result.best.save(args.profile_out)
    print(f"{len(result.trials)} offline evaluations; report: {args.report}")
    for axis, evidence in result.axis_evidence.items():
        print(f"  {axis}: {evidence['effect']} ({evidence['compared_contexts']} compared contexts)")
    if result.best is None:
        print("No configuration completed a transaction; no profile exported.")
    elif args.profile_out:
        print(f"Candidate for live verification: {args.profile_out}")
    print("This ranks transfers of staged gold; it does not establish a better live economy.")


if __name__ == "__main__":
    main()
