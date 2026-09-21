from __future__ import annotations

"""6'li Ganyan combination optimizer.

This module does not claim to predict outcomes with certainty. It converts
race-level win probabilities into budget-constrained combinations and keeps
a controlled number of lower-market-probability alternatives when their model
probability is materially higher than their market-implied probability.
"""

import itertools
import json
from pathlib import Path
from typing import Iterable

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"


def _p(row: dict) -> float:
    try:
        return max(0.0, float(row.get("model_probability") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _agf(row: dict) -> float:
    try:
        return max(0.0, float(row.get("agf_score") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _edge(row: dict) -> float:
    try:
        return float(row.get("edge") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _race_key(row: dict) -> str:
    return str(row.get("race") or row.get("race_id") or "unknown")


def _rank_score(row: dict) -> float:
    # Probability is primary; positive model-vs-market edge provides a
    # controlled value signal; a small surprise bonus prevents systematic
    # over-selection of only high-AGF favourites.
    p = _p(row)
    edge = max(-0.25, min(0.25, _edge(row)))
    surprise = 0.03 if _agf(row) < 10 and edge > 0.03 else 0.0
    quality = max(0.35, min(1.0, float(row.get("data_quality", 1.0) or 1.0)))
    return p * quality + 0.35 * edge + surprise


def group_races(rows: Iterable[dict]) -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(_race_key(row), []).append(row)
    return [sorted(v, key=_rank_score, reverse=True) for _, v in sorted(groups.items(), key=lambda x: x[0])]


def _field_uncertainty(race: list[dict]) -> float:
    if not race:
        return 1.0
    probs = [_p(r) for r in race]
    top = max(probs)
    second = sorted(probs, reverse=True)[1] if len(probs) > 1 else 0.0
    margin = max(0.0, top - second)
    entropy = -sum(p * __import__("math").log(max(p, 1e-12)) for p in probs)
    max_entropy = __import__("math").log(max(len(probs), 2))
    return max(0.0, min(1.0, 0.65 * (1.0 - margin / 0.30) + 0.35 * (entropy / max_entropy)))


def select_leg_candidates(race: list[dict], target_count: int | None = None, max_horses: int = 6) -> list[dict]:
    if not race:
        return []
    ranked = sorted(race, key=_rank_score, reverse=True)
    if target_count is None:
        target_count = 2 + int(round(_field_uncertainty(race) * 4))
    target_count = max(1 if len(ranked) == 1 else 2, min(max_horses, target_count, len(ranked)))
    selected = ranked[:target_count]
    if len(ranked) > target_count:
        surprises = [r for r in ranked[target_count:] if _edge(r) > 0.04 and _agf(r) < 15]
        if surprises:
            selected[-1] = max(surprises, key=_rank_score)
    return sorted(selected, key=_rank_score, reverse=True)


def _banko_eligible(race: list[dict]) -> bool:
    """Allow a single only when the model has a meaningful separation."""
    if len(race) <= 1:
        return True
    ranked = sorted(race, key=_rank_score, reverse=True)
    top = _p(ranked[0])
    second = _p(ranked[1])
    margin = top - second
    # A single is allowed only with both probability and separation.
    # This is a selection rule, not a claim of certainty.
    return top >= 0.30 and margin >= 0.10


def _candidate_value(row: dict) -> float:
    """Selection value used only for allocating scarce coupon coverage."""
    p = _p(row)
    quality = max(0.35, min(1.0, float(row.get("data_quality", 1.0) or 1.0)))
    edge = max(0.0, min(0.20, _edge(row)))
    surprise = 0.02 if _agf(row) < 10 and edge > 0.03 else 0.0
    return p * (0.75 + 0.25 * quality) + 0.20 * edge + surprise


def _coverage_probability(race: list[dict], count: int) -> float:
    """Approximate probability that the winner is covered by selected runners."""
    ranked = sorted(race, key=_candidate_value, reverse=True)
    return min(0.999, sum(_p(r) for r in ranked[:count]))


def _product(counts: list[int]) -> int:
    value = 1
    for c in counts:
        value *= max(1, c)
    return value


def _coverage_candidates(
    races: list[list[dict]], budget: int, unit_cost: int, max_horses: int = 8
) -> list[list[dict]]:
    """Allocate runner counts from race structure, not a fixed template.

    A single is used only when separation is meaningful. Other legs start
    with two. Each additional runner is purchased where it increases covered
    model probability most efficiently under the combination budget.
    """
    target = max(1, budget // max(unit_cost, 1))
    counts = [
        1 if _banko_eligible(race) else min(2, len(race))
        for race in races
    ]
    counts = [
        max(1, min(c, min(max_horses, len(race))))
        for c, race in zip(counts, races)
    ]

    while True:
        current_product = _product(counts)
        choices = []
        for i, race in enumerate(races):
            limit = min(max_horses, len(race))
            if counts[i] >= limit:
                continue
            new_counts = list(counts)
            new_counts[i] += 1
            new_product = _product(new_counts)
            if new_product > target:
                continue

            before = _coverage_probability(race, counts[i])
            after = _coverage_probability(race, counts[i] + 1)
            marginal = max(0.0, after - before)
            growth = max(1.0, new_product / max(current_product, 1))
            uncertainty = _field_uncertainty(race)
            score = (marginal * (0.65 + 0.35 * uncertainty)) / growth
            choices.append((score, i))

        if not choices:
            break
        _, idx = max(choices)
        counts[idx] += 1

    return [
        select_leg_candidates(race, target_count=counts[i], max_horses=max_horses)
        for i, race in enumerate(races)
    ]

def combination_score(combo: tuple[dict, ...]) -> float:
    # Joint score is proportional to the product of leg probabilities.
    value = 1.0
    for row in combo:
        value *= max(_p(row), 1e-9)
    return value


def optimize(rows: list[dict], budget: int = 720, unit_cost: int = 1) -> dict:
    races = group_races(rows)
    if len(races) < 6:
        return {
            "status": "insufficient_races",
            "race_count": len(races),
            "required_races": 6,
            "budget": budget,
            "combinations": [],
        }

    races = races[:6]
    # Candidate counts are reduced before Cartesian expansion.
    candidates = _coverage_candidates(races, budget, unit_cost)
    counts = [len(x) for x in candidates]

    all_combos = []
    for combo in itertools.product(*candidates):
        score = combination_score(combo)
        all_combos.append((score, combo))

    all_combos.sort(key=lambda x: x[0], reverse=True)
    max_combos = max(1, budget // max(unit_cost, 1))
    chosen = all_combos[:max_combos]

    combinations = []
    for score, combo in chosen:
        combinations.append({
            "joint_model_score": round(score, 10),
            "selections": [
                {
                    "race": _race_key(row),
                    "start": row.get("start"),
                    "horse": row.get("horse"),
                    "probability": round(_p(row), 6),
                    "agf": round(_agf(row), 3),
                    "edge": row.get("edge"),
                }
                for row in combo
            ],
        })

    primary_product = _product(counts)
    primary_probability = 1.0
    for race, selected in zip(races, candidates):
        primary_probability *= _coverage_probability(race, len(selected))

    return {
        "status": "ok",
        "race_count": 6,
        "budget": budget,
        "unit_cost": unit_cost,
        "candidate_counts": counts,
        "requested_combinations": max_combos,
        "returned_combinations": len(combinations),
        "budget_used": primary_product * unit_cost,
        "primary_coverage_estimate": round(primary_probability, 8),
        "primary_coupon": [
            [r.get("horse") for r in selected]
            for selected in candidates
        ],
        "legs": [
            {
                "race": _race_key(race[0]) if race else str(i + 1),
                "selection_count": len(candidates[i]),
                "selections": [
                    {"horse": r.get("horse"), "start": r.get("start"),
                     "probability": round(_p(r), 6), "agf": round(_agf(r), 3),
                     "edge": r.get("edge")}
                    for r in candidates[i]
                ],
            }
            for i, race in enumerate(races)
        ],
        "coverage_note": "Aday sayıları yarış belirsizliğine ve bütçeye göre dağıtıldı; tek at zorunlu değil.",
        "combinations": combinations,
    }


def build_budgets(rows: list[dict], budgets=(240, 720, 1440)) -> dict:
    return {str(b): optimize(rows, budget=b) for b in budgets}


def main() -> None:
    source = DATA / "advanced_ranked_horses.json"
    if not source.exists():
        print("Run main.py first.")
        return
    rows = json.loads(source.read_text(encoding="utf-8"))
    result = build_budgets(rows)
    (DATA / "sixli_coupons.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("6'li Ganyan optimizer completed.")


if __name__ == "__main__":
    main()
