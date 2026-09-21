from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from collections import defaultdict

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

def build_leakage_free_rows():
    source = DATA / "historical_results.csv"
    if not source.exists():
        return []
    raw = [r for r in csv.DictReader(source.open("r", newline="", encoding="utf-8"))]
    raw.sort(key=lambda r: (r.get("race_date",""), r.get("track",""), int(num(r.get("race_number"))), r.get("horse","")))
    by_horse = defaultdict(list)
    for r in raw:
        by_horse[r["horse"].strip().upper()].append(r)

    rows = []
    for r in raw:
        name = r["horse"].strip().upper()
        prior = [x for x in by_horse[name] if x["race_date"] < r["race_date"]]
        weight = num(r.get("weight")) or 60.0
        hps = [num(x.get("hp")) for x in [r] if num(x.get("hp")) > 0]
        hp = num(r.get("hp"))
        target_track = r.get("track","")
        distance = num(r.get("distance"))

        def form_score(vals, step=15.0):
            vals = [x for x in vals if x > 0]
            if not vals:
                return 0.0
            return sum(max(0.0, 100.0 - (x - 1) * step) for x in vals[-8:]) / len(vals[-8:])

        def scoped(predicate):
            ps = [num(x.get("finish_position")) for x in prior if predicate(x) and num(x.get("finish_position")) > 0]
            return form_score(ps, 18.0) if ps else 50.0

        rows.append({
            "race_id": r["race_id"],
            "race_date": r["race_date"],
            "race_number": r.get("race_number"),
            "track": target_track,
            "distance": distance,
            "horse": r["horse"],
            "finish_position": int(num(r["finish_position"])),
            "recent_form": form_score([num(x.get("finish_position")) for x in prior]),
            "track_form": scoped(lambda x: x.get("track","") == target_track),
            "distance_form": scoped(lambda x: abs(num(x.get("distance")) - distance) <= 200),
            "jockey_form": scoped(lambda x: x.get("jockey","").strip() == r.get("jockey","").strip()),
            "trainer_form": scoped(lambda x: x.get("trainer","").strip() == r.get("trainer","").strip()),
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
    ex = [math.exp(v-m) for v in z]
    s = sum(ex) or 1.0
    probs = [v/s for v in ex]
    prior = 1.0 / len(group)
    probs = [(p*2.0 + prior)/3.0 for p in probs]
    s = sum(probs)
    return [p/s for p in probs]

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

    for race_id, group in grouped.items():
        if len(group) < 2:
            continue
        probs = heuristic_probs(group)
        for r,p in zip(group, probs):
            r["_p"] = p
        winner = next((i for i,r in enumerate(group) if r["finish_position"] == 1), None)
        if winner is None:
            continue
        evaluated += 1
        order = sorted(range(len(group)), key=lambda i: group[i]["_p"], reverse=True)
        if order[0] == winner:
            top1 += 1
        if winner in order[:3]:
            top3 += 1
        for i,r in enumerate(group):
            y = 1.0 if i == winner else 0.0
            p = min(1-1e-9, max(1e-9, r["_p"]))
            brier.append((p-y)**2)
            logloss.append(-(y*math.log(p)+(1-y)*math.log(1-p)))

    return {
        "data_rows": len(rows),
        "evaluated_races": evaluated,
        "top1_hit_rate": top1/evaluated if evaluated else None,
        "top3_coverage": top3/evaluated if evaluated else None,
        "brier_score": sum(brier)/len(brier) if brier else None,
        "log_loss": sum(logloss)/len(logloss) if logloss else None,
        "method": "strict pre-date leakage-free heuristic backtest",
        "note": "Predictions for each race use only records dated before the target race; no realized target result is used as an input.",
    }

if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
