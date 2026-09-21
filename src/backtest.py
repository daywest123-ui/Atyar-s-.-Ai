from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

FEATURES = [
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score",
    "agf_score", "history_count", "hp",
]


def num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def load_rows():
    source = DATA / "historical_results.csv"
    if not source.exists():
        return []
    return list(csv.DictReader(source.open("r", newline="", encoding="utf-8")))


def form_score(vals, step=15.0):
    vals = [x for x in vals if x > 0]
    if not vals:
        return 0.0
    vals = vals[-8:]
    return sum(max(0.0, 100.0 - (x - 1) * step) for x in vals) / len(vals)


def build_leakage_free_rows():
    raw = load_rows()
    raw.sort(key=lambda r: (
        r.get("race_date", ""),
        r.get("track", ""),
        int(num(r.get("race_number"))),
        r.get("horse", ""),
    ))

    by_horse = defaultdict(list)
    for r in raw:
        by_horse[r.get("horse", "").strip().upper()].append(r)

    rows = []
    for r in raw:
        name = r.get("horse", "").strip().upper()
        # Conservative cutoff: only dates strictly before the target date.
        prior = [x for x in by_horse[name] if x.get("race_date", "") < r.get("race_date", "")]
        target_track = r.get("track", "")
        target_distance = num(r.get("distance"))
        weight = num(r.get("weight")) or 60.0
        hp = num(r.get("hp"))

        def scoped(predicate):
            vals = [
                num(x.get("finish_position"))
                for x in prior
                if predicate(x) and num(x.get("finish_position")) > 0
            ]
            return form_score(vals, 18.0) if vals else 50.0

        rows.append({
            "race_id": r.get("race_id", ""),
            "race_date": r.get("race_date", ""),
            "race_number": r.get("race_number", ""),
            "track": target_track,
            "distance": target_distance,
            "horse": r.get("horse", ""),
            "finish_position": int(num(r.get("finish_position"))),
            "jockey": r.get("jockey", ""),
            "trainer": r.get("trainer", ""),
            "recent_form": form_score([num(x.get("finish_position")) for x in prior]),
            "track_form": scoped(lambda x: x.get("track", "") == target_track),
            "distance_form": scoped(lambda x: abs(num(x.get("distance")) - target_distance) <= 200),
            "jockey_form": scoped(lambda x: x.get("jockey", "").strip() == r.get("jockey", "").strip()),
            "trainer_form": scoped(lambda x: x.get("trainer", "").strip() == r.get("trainer", "").strip()),
            "weight_score": max(0.0, min(100.0, 100.0 - abs(weight - 60.0) * 4)),
            "agf_score": min(100.0, max(0.0, num(r.get("agf_score")))),
            "history_count": len(prior),
            "hp": hp,
        })
    return rows


def heuristic_probs(group):
    from advanced_model import baseline_score

    scores = [baseline_score(r, group) for r in group]
    z = [x / 6.0 for x in scores]
    m = max(z)
    ex = [math.exp(v - m) for v in z]
    total = sum(ex) or 1.0
    probs = [v / total for v in ex]

    prior = 1.0 / len(group)
    probs = [(p * 2.0 + prior) / 3.0 for p in probs]
    total = sum(probs) or 1.0
    return [p / total for p in probs]


def _race_groups(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["race_id"]].append(r)
    return grouped


def _binary_metrics(pred_rows):
    grouped = _race_groups(pred_rows)
    top1 = top3 = races = 0
    brier = []
    logloss = []

    for _, group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        winner = next((i for i, r in enumerate(group) if r["finish_position"] == 1), None)
        if winner is None:
            continue
        races += 1
        order = sorted(range(len(group)), key=lambda i: group[i]["probability"], reverse=True)
        top1 += int(order[0] == winner)
        top3 += int(winner in order[:3])

        for i, r in enumerate(group):
            p = min(1 - 1e-9, max(1e-9, float(r["probability"])))
            y = 1.0 if i == winner else 0.0
            brier.append((p - y) ** 2)
            logloss.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))

    return {
        "races": races,
        "top1_hit_rate": top1 / races if races else None,
        "top3_coverage": top3 / races if races else None,
        "brier_score": sum(brier) / len(brier) if brier else None,
        "log_loss": sum(logloss) / len(logloss) if logloss else None,
    }


def _calibration(pred_rows, bins=10):
    grouped = _race_groups(pred_rows)
    pairs = []
    for group in grouped.values():
        for r in group:
            pairs.append((float(r["probability"]), 1 if r["finish_position"] == 1 else 0))

    out = []
    for b in range(bins):
        lo = b / bins
        hi = (b + 1) / bins
        bucket = [(p, y) for p, y in pairs if lo <= p < hi or (b == bins - 1 and p <= hi)]
        if not bucket:
            continue
        out.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "count": len(bucket),
            "mean_predicted": round(sum(p for p, _ in bucket) / len(bucket), 6),
            "observed_win_rate": round(sum(y for _, y in bucket) / len(bucket), 6),
        })
    return out


def evaluate_heuristic(rows):
    out = []
    for _, group in sorted(_race_groups(rows).items()):
        if len(group) < 2:
            continue
        probs = heuristic_probs(group)
        for r, p in zip(group, probs):
            item = dict(r)
            item["probability"] = p
            out.append(item)
    return _binary_metrics(out), _calibration(out)


def evaluate_lightgbm(rows, min_train_dates=12):
    """Expanding-window OOS LightGBM.

    For each target date, only earlier dates are eligible for training.
    A trailing validation slice inside that past window calibrates probabilities.
    The target date is never used in fitting or calibration.
    """
    try:
        import pandas as pd
        import lightgbm as lgb
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:
        return {"error": f"LightGBM/scikit-learn unavailable: {exc}"}

    df = pd.DataFrame(rows)
    df["race_date"] = pd.to_datetime(df["race_date"], errors="coerce")
    df = df.dropna(subset=["race_date", "finish_position"]).copy()
    df["race_date_key"] = df["race_date"].dt.date
    dates = sorted(df["race_date_key"].unique())
    if len(dates) <= min_train_dates:
        return {"error": "Not enough historical dates for rolling OOS evaluation."}

    all_predictions = []
    importances = []
    skipped_dates = []

    params = dict(
        n_estimators=300,
        learning_rate=0.035,
        num_leaves=15,
        max_depth=-1,
        subsample=.85,
        colsample_bytree=.85,
        random_state=42,
        verbosity=-1,
    )

    for target_idx in range(min_train_dates, len(dates)):
        target_date = dates[target_idx]
        past_dates = dates[:target_idx]

        # Reserve the last 20% of the past dates solely for calibration.
        cal_count = max(3, int(len(past_dates) * 0.20))
        cal_dates = set(past_dates[-cal_count:])
        train_dates = set(past_dates[:-cal_count])
        if len(train_dates) < 5:
            skipped_dates.append(str(target_date))
            continue

        train = df[df["race_date_key"].isin(train_dates)].copy()
        cal = df[df["race_date_key"].isin(cal_dates)].copy()
        target = df[df["race_date_key"] == target_date].copy()

        if train["finish_position"].eq(1).sum() < 20 or cal["finish_position"].eq(1).sum() < 5:
            skipped_dates.append(str(target_date))
            continue

        X_train = train[FEATURES].fillna(0.0)
        y_train = (train["finish_position"].astype(float) == 1).astype(int)
        X_cal = cal[FEATURES].fillna(0.0)
        y_cal = (cal["finish_position"].astype(float) == 1).astype(int)
        X_target = target[FEATURES].fillna(0.0)

        model = lgb.LGBMClassifier(**params)
        model.fit(X_train, y_train)

        raw_cal = model.predict_proba(X_cal)[:, 1]
        calibrator = LogisticRegression(C=1.0, max_iter=1000)
        calibrator.fit(raw_cal.reshape(-1, 1), y_cal)

        # Refit on every known race before the target date.
        known = df[df["race_date_key"] < target_date].copy()
        final_model = lgb.LGBMClassifier(**params)
        final_model.fit(known[FEATURES].fillna(0.0),
                        (known["finish_position"].astype(float) == 1).astype(int))

        raw_target = final_model.predict_proba(X_target)[:, 1]
        calibrated = calibrator.predict_proba(raw_target.reshape(-1, 1))[:, 1]
        target = target.copy()
        target["raw_probability"] = raw_target
        target["probability"] = calibrated

        # Convert independent horse scores into a race probability distribution.
        for race_id, idxs in target.groupby("race_id").groups.items():
            idxs = list(idxs)
            scores = [max(float(target.loc[i, "probability"]), 1e-9) for i in idxs]
            total = sum(scores)
            for i, p in zip(idxs, scores):
                target.loc[i, "probability"] = p / total

        for _, r in target.iterrows():
            all_predictions.append({
                "race_id": r["race_id"],
                "race_date": str(target_date),
                "horse": r["horse"],
                "finish_position": int(r["finish_position"]),
                "probability": float(r["probability"]),
            })

        imp = getattr(final_model, "feature_importances_", None)
        if imp is not None:
            importances.append(dict(zip(FEATURES, [float(x) for x in imp])))

    if not all_predictions:
        return {"error": "No valid OOS target dates were evaluated.", "skipped_dates": skipped_dates}

    metrics = _binary_metrics(all_predictions)
    calibration = _calibration(all_predictions)

    feature_importance = {}
    if importances:
        for feature in FEATURES:
            vals = [x[feature] for x in importances]
            feature_importance[feature] = round(sum(vals) / len(vals), 6)
        feature_importance = dict(sorted(feature_importance.items(), key=lambda kv: kv[1], reverse=True))

    return {
        "method": "expanding-window LightGBM, strict pre-date OOS",
        "metrics": metrics,
        "calibration": calibration,
        "feature_importance_mean_gain_proxy": feature_importance,
        "oos_rows": len(all_predictions),
        "oos_dates": len({x["race_date"] for x in all_predictions}),
        "skipped_dates": skipped_dates,
        "note": "For each target date, training uses only earlier dates. The final learner is refit on all known pre-target races. Calibration is fitted on a trailing historical slice that also precedes the target date. Same-day history is excluded.",
    }


def evaluate():
    rows = build_leakage_free_rows()
    heuristic_metrics, heuristic_calibration = evaluate_heuristic(rows)

    result = {
        "data_rows": len(rows),
        "evaluated_races_heuristic": heuristic_metrics["races"],
        "heuristic": heuristic_metrics,
        "heuristic_calibration": heuristic_calibration,
    }

    lgbm = evaluate_lightgbm(rows)
    result["lightgbm_oos"] = lgbm

    if isinstance(lgbm, dict) and "metrics" in lgbm:
        same_races = lgbm["metrics"]["races"]
        result["comparison"] = {
            "heuristic_top1": heuristic_metrics["top1_hit_rate"],
            "lightgbm_top1": lgbm["metrics"]["top1_hit_rate"],
            "heuristic_top3": heuristic_metrics["top3_coverage"],
            "lightgbm_top3": lgbm["metrics"]["top3_coverage"],
            "heuristic_brier": heuristic_metrics["brier_score"],
            "lightgbm_brier": lgbm["metrics"]["brier_score"],
            "heuristic_log_loss": heuristic_metrics["log_loss"],
            "lightgbm_log_loss": lgbm["metrics"]["log_loss"],
            "lightgbm_evaluated_races": same_races,
        }

    result["method"] = "strict pre-date leakage-free historical backtest"
    result["data_note"] = "historical_results.csv contains 2026-08-22 through 2026-09-20 Turkish race results. Same-day prior races are excluded because reliable intra-day ordering is not encoded in the training features."
    return result


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
