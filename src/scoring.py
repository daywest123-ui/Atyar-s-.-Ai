from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class HorseFeatures:
    recent_form: float = 0.0
    track_form: float = 0.0
    distance_form: float = 0.0
    jockey_form: float = 0.0
    trainer_form: float = 0.0
    weight_score: float = 0.0
    agf_score: float = 0.0


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def horse_score(f: HorseFeatures) -> float:
    """Transparent baseline score. Weights can be recalibrated with historical results."""
    score = (
        0.30 * f.recent_form
        + 0.15 * f.track_form
        + 0.15 * f.distance_form
        + 0.15 * f.jockey_form
        + 0.10 * f.trainer_form
        + 0.05 * f.weight_score
        + 0.10 * f.agf_score
    )
    return round(clamp(score), 2)


def rank_horses(rows: list[dict]) -> list[dict]:
    ranked = []
    for row in rows:
        features = HorseFeatures(**{k: float(row.get(k, 0) or 0) for k in HorseFeatures.__annotations__})
        item = dict(row)
        item["score"] = horse_score(features)
        ranked.append(item)
    return sorted(ranked, key=lambda x: x["score"], reverse=True)
