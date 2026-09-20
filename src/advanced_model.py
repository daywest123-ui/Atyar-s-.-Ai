from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

FEATURES = (
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score", "agf_score", "hp",
)


def _f(row: dict, key: str) -> float:
    try:
        return float(row.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def baseline_score(row: dict, field: list[dict] | None = None) -> float:
    weights = {
        "recent_form": .34, "track_form": .10, "distance_form": .10,
        "jockey_form": .10, "trainer_form": .08,
        "weight_score": .06, "agf_score": .07, "hp": .15,
    }
    score = sum(weights[k] * _f(row, k) for k in weights if k != "hp")
    hp = _f(row, "hp")
    hps = [_f(r, "hp") for r in (field or []) if _f(r, "hp") > 0]
    if hps and hp > 0:
        lo, hi = min(hps), max(hps)
        hp_norm = 50.0 if hi <= lo else 100.0 * (hp - lo) / (hi - lo)
    else:
        hp_norm = 50.0
    return score + weights["hp"] * hp_norm


def _softmax(values: list[float], temperature: float = 8.0) -> list[float]:
    if not values:
        return []
    z = [v / max(temperature, 1e-6) for v in values]
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e) or 1.0
    return [v / s for v in e]


def _bayesian_shrink(prob: float, prior: float, strength: float = 6.0) -> float:
    # Beta-style shrinkage keeps small/uncertain fields closer to the race prior.
    return (prob * strength + prior * strength) / (2.0 * strength)


def _try_lightgbm(rows: list[dict]) -> list[float] | None:
    """Use LightGBM when a labelled training.csv is supplied; otherwise return None.

    Expected columns: race_id, finish_position and the FEATURES above.
    This deliberately avoids training on today's result data.
    """
    try:
        import pandas as pd
        import lightgbm as lgb
    except ImportError:
        return None

    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "data" / "training.csv"
    if not path.exists():
        return None

    df = pd.read_csv(path)
    needed = {"race_id", "finish_position", *FEATURES}
    if not needed.issubset(df.columns) or len(df) < 100:
        return None

    df = df.dropna(subset=["race_id", "finish_position"])
    df["target"] = (df["finish_position"].astype(float) == 1).astype(int)
    if df["target"].sum() < 10:
        return None

    model = lgb.LGBMClassifier(
        n_estimators=250,
        learning_rate=0.04,
        num_leaves=15,
        subsample=.85,
        colsample_bytree=.85,
        random_state=42,
        verbosity=-1,
    )
    model.fit(df[list(FEATURES)], df["target"])

    return model.predict_proba(
        pd.DataFrame([{k: _f(r, k) for k in FEATURES} for r in rows])
    )[:, 1].tolist()


def enrich(rows: list[dict]) -> list[dict]:
    """Add model probability, fair odds, edge and confidence to today's horses."""
    if not rows:
        return []

    ml = _try_lightgbm(rows)
    raw = ml if ml is not None else [baseline_score(r) for r in rows]

    by_race: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        race = str(row.get("race") or row.get("race_id") or "unknown")
        by_race[race].append(i)

    out = [dict(r) for r in rows]
    for race, indexes in by_race.items():
        if ml is None:
            field = [rows[i] for i in indexes]
            race_raw = [baseline_score(rows[i], field) for i in indexes]
        else:
            race_raw = [raw[i] for i in indexes]
        probs = _softmax(race_raw, temperature=6.0)
        prior = 1.0 / max(len(indexes), 1)
        # Mild shrinkage; the previous strong shrinkage flattened the field.
        probs = [(p * 2.0 + prior) / 3.0 for p in probs]
        total = sum(probs) or 1.0
        probs = [p / total for p in probs]

        for idx, p in zip(indexes, probs):
            item = out[idx]
            item["model_probability"] = round(p, 6)
            item["fair_odds"] = round(1.0 / p, 3) if p > 0 else None
            item["model_score"] = round(raw[idx], 3)
            item["confidence"] = round(max(0.0, min(1.0, (p - prior) / max(1-prior, .001))), 4)

            try:
                odds = float(item.get("odds") or item.get("gny") or 0)
            except (TypeError, ValueError):
                odds = 0.0
            item["market_odds"] = odds or None
            item["edge"] = round(p - (1.0 / odds), 6) if odds > 1 else None

    return sorted(out, key=lambda x: (str(x.get("race")), -x.get("model_probability", 0)))


def harville_order_probability(probs: Iterable[float], order: Iterable[int]) -> float:
    """Harville approximation for an exact finishing order.

    probs are win probabilities; order contains zero-based horse indexes.
    """
    p = list(probs)
    remaining = sum(p)
    result = 1.0
    for idx in order:
        if idx < 0 or idx >= len(p) or remaining <= 0:
            return 0.0
        result *= p[idx] / remaining
        remaining -= p[idx]
    return result


def race_summary(rows: list[dict]) -> dict:
    if not rows:
        return {}
    ranked = sorted(rows, key=lambda x: x.get("model_probability", 0), reverse=True)
    return {
        "race": str(rows[0].get("race") or rows[0].get("race_id") or "unknown"),
        "field_size": len(rows),
        "top3": [
            {
                "horse": r.get("horse"),
                "start": r.get("start"),
                "probability": r.get("model_probability"),
                "fair_odds": r.get("fair_odds"),
                "edge": r.get("edge"),
            }
            for r in ranked[:3]
        ],
        "skip_gate": bool(ranked and ranked[0].get("model_probability", 0) < 0.35),
    }
