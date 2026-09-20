from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable

FEATURES = (
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score", "agf_score", "hp", "history_count",
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
        "weight_score": .06, "agf_score": .07, "hp": .15, "history_count": .05,
    }
    available = []
    for k in ("recent_form", "track_form", "distance_form", "jockey_form", "trainer_form", "weight_score", "agf_score"):
        v = _f(row, k)
        if k in {"track_form", "distance_form", "jockey_form", "trainer_form"} and _f(row, "history_count") <= 0:
            continue
        if k == "agf_score" and v <= 0:
            continue
        if k == "recent_form" and v <= 0:
            continue
        available.append((k, v))
    denom = sum(weights[k] for k, _ in available)
    score = sum(weights[k] * v for k, v in available) / max(denom, 1e-9)
    hp = _f(row, "hp")
    hps = [_f(r, "hp") for r in (field or []) if _f(r, "hp") > 0]
    if hps and hp > 0:
        lo, hi = min(hps), max(hps)
        hp_norm = 50.0 if hi <= lo else 100.0 * (hp - lo) / (hi - lo)
    else:
        hp_norm = 50.0
    score = 0.85 * score + 0.15 * hp_norm
    # Historical depth slightly raises/lowers the score only as a confidence
    # modifier, not as a performance claim.
    depth = min(_f(row, "history_count") / 12.0, 1.0)
    return score * (0.92 + 0.08 * depth)


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
    """Train only on historical races and calibrate on a later validation period."""
    try:
        import pandas as pd
        import lightgbm as lgb
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return None

    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "data" / "training.csv"
    if not path.exists():
        return None

    df = pd.read_csv(path)
    needed = {"race_id", "race_date", "finish_position", *FEATURES}
    if not needed.issubset(df.columns):
        return None
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    df = df.dropna(subset=["race_date", "finish_position"]).sort_values("race_date")
    if len(df) < 300:
        return None
    race_dates = sorted(df["race_date"].dt.date.unique())
    if len(race_dates) < 20:
        return None

    cut = max(10, int(len(race_dates) * 0.80))
    train_dates = set(race_dates[:cut])
    valid_dates = set(race_dates[cut:])
    train = df[df["race_date"].dt.date.isin(train_dates)].copy()
    valid = df[df["race_date"].dt.date.isin(valid_dates)].copy()
    if train["finish_position"].eq(1).sum() < 20 or valid["finish_position"].eq(1).sum() < 5:
        return None

    X_train = train[list(FEATURES)].fillna(0.0)
    y_train = (train["finish_position"].astype(float) == 1).astype(int)
    X_valid = valid[list(FEATURES)].fillna(0.0)
    y_valid = (valid["finish_position"].astype(float) == 1).astype(int)

    params = dict(
        n_estimators=300, learning_rate=0.035, num_leaves=15,
        max_depth=-1, subsample=.85, colsample_bytree=.85,
        random_state=42, verbosity=-1,
    )
    model = lgb.LGBMClassifier(**params)
    model.fit(X_train, y_train)

    # Calibration is fitted strictly on later validation races.
    raw_valid = model.predict_proba(X_valid)[:, 1]
    calibrator = LogisticRegression(C=1.0, max_iter=1000)
    calibrator.fit(raw_valid.reshape(-1, 1), y_valid)

    # Refit the base learner on all historical races available before today.
    all_x = df[list(FEATURES)].fillna(0.0)
    all_y = (df["finish_position"].astype(float) == 1).astype(int)
    final_model = lgb.LGBMClassifier(**params)
    final_model.fit(all_x, all_y)

    current = pd.DataFrame([{k: _f(r, k) for k in FEATURES} for r in rows]).fillna(0.0)
    raw_current = final_model.predict_proba(current)[:, 1]
    return calibrator.predict_proba(raw_current.reshape(-1, 1))[:, 1].tolist()

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
            available = 0
            if _f(item, "recent_form") > 0: available += 1
            if _f(item, "hp") > 0: available += 1
            if _f(item, "weight") > 0: available += 1
            if _f(item, "agf_score") > 0: available += 1
            if _f(item, "history_count") > 0: available += 3
            item["data_quality"] = round(min(1.0, available / 7.0), 3)
            item["risk_flag"] = "low_data" if item["data_quality"] < 0.45 else ("medium_data" if item["data_quality"] < 0.70 else "normal")

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
        "data_quality": round(sum(float(r.get("data_quality", 0) or 0) for r in ranked) / len(ranked), 3),
        "banko_candidate": bool(
            ranked and len(ranked) > 1
            and float(ranked[0].get("model_probability", 0) or 0) >= 0.30
            and (float(ranked[0].get("model_probability", 0) or 0) - float(ranked[1].get("model_probability", 0) or 0)) >= 0.10
            and float(ranked[0].get("data_quality", 0) or 0) >= 0.60
        ),
    }
