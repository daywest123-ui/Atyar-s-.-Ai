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

def evaluate():
    rows = build_leakage_free_rows()
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["race_id"]].append(r)

    evaluated = 0
    top1 = 0
    top3 = 0
    brier = []
    logloss = []
    by_date = defaultdict(lambda: {"races": 0, "top1": 0, "top3": 0})

    for race_id, group in sorted(grouped.items()):
        if len(group) < 2:
            continue

        probs = heuristic_probs(group)
        for r, p in zip(group, probs):
            r["_p"] = p

        winner = next((i for i, r in enumerate(group) if r["finish_position"] == 1), None)
        if winner is None:
            continue

        evaluated += 1
        date_key = group[0]["race_date"]
        by_date[date_key]["races"] += 1

        order = sorted(range(len(group)), key=lambda i: group[i]["_p"], reverse=True)
        if order[0] == winner:
            top1 += 1
            by_date[date_key]["top1"] += 1
        if winner in order[:3]:
            top3 += 1
            by_date[date_key]["top3"] += 1

        for i, r in enumerate(group):
            y = 1.0 if i == winner else 0.0
            p = min(1 - 1e-9, max(1e-9, r["_p"]))
            brier.append((p - y) ** 2)
            logloss.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))

    date_metrics = {}
    for d, v in sorted(by_date.items()):
        date_metrics[d] = {
            "races": v["races"],
            "top1_hit_rate": v["top1"] / v["races"] if v["races"] else None,
            "top3_coverage": v["top3"] / v["races"] if v["races"] else None,
        }

    return {
        "data_rows": len(rows),
        "evaluated_races": evaluated,
        "top1_hit_rate": top1 / evaluated if evaluated else None,
        "top3_coverage": top3 / evaluated if evaluated else None,
        "brier_score": sum(brier) / len(brier) if brier else None,
        "log_loss": sum(logloss) / len(logloss) if logloss else None,
        "date_metrics": date_metrics,
        "method": "strict pre-date leakage-free heuristic backtest",
        "note": "Each target race uses only records with earlier race dates for horse-history features. Same-day history is excluded because race ordering is not used as a feature.",
    }

if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
