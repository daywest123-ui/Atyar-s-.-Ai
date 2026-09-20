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
    return p + 0.35 * edge + surprise


def group_races(rows: Iterable[dict]) -> list[list[dict]]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(_race_key(row), []).append(row)
    return [sorted(v, key=_rank_score, reverse=True) for _, v in sorted(groups.items(), key=lambda x: x[0])]


def select_leg_candidates(race: list[dict], max_horses: int = 6) -> list[dict]:
    if not race:
        return []
    ranked = sorted(race, key=_rank_score, reverse=True)
    # Keep enough candidates to cover uncertainty, but cap the explosion.
    selected = ranked[:max_horses]
    if len(ranked) > max_horses:
        surprises = [r for r in ranked[max_horses:] if _edge(r) > 0.04 and _agf(r) < 15]
        if surprises:
            selected[-1] = max(surprises, key=_rank_score)
    return sorted(selected, key=_rank_score, reverse=True)


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
    candidates = [select_leg_candidates(r, 6) for r in races]
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

    return {
        "status": "ok",
        "race_count": 6,
        "budget": budget,
        "unit_cost": unit_cost,
        "candidate_counts": counts,
        "requested_combinations": max_combos,
        "returned_combinations": len(combinations),
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
