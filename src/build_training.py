from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

FEATURES = [
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score", "agf_score", "history_count", "hp",
]

def num(v):
    try:
        return float(str(v).replace("%","").replace(",","."))
    except (TypeError, ValueError):
        return 0.0

def form_score(positions, step=15.0):
    vals = [max(0.0, 100.0 - (p - 1) * step) for p in positions[-8:] if p]
    return sum(vals) / len(vals) if vals else 0.0

def rate(rows, predicate):
    vals = []
    for r in rows:
        if predicate(r):
            p = int(r["finish_position"])
            vals.append(max(0.0, 100.0 - (p - 1) * 18.0))
    return sum(vals) / len(vals) if vals else 50.0

def build():
    source = DATA / "historical_results.csv"
    if not source.exists():
        raise SystemExit("historical_results.csv yok; önce results_backfill.py çalıştırılmalı.")
    raw = list(csv.DictReader(source.open("r", newline="", encoding="utf-8")))
    raw.sort(key=lambda r: (r.get("race_date",""), r.get("race_id",""), r.get("horse","")))
    by_horse = defaultdict(list)
    for r in raw:
        by_horse[r["horse"].strip().upper()].append(r)

    rows = []
    for r in raw:
        name = r["horse"].strip().upper()
        prior = [x for x in by_horse[name] if x["race_date"] < r["race_date"]]
        target_track = r.get("track","")
        target_distance = num(r.get("distance"))
        prior_positions = [int(x["finish_position"]) for x in prior]
        weight = num(r.get("weight")) or 60.0
        jockey = r.get("jockey","").strip()
        trainer = r.get("trainer","").strip()
        rows.append({
            "race_id": r["race_id"],
            "race_date": r["race_date"],
            "finish_position": int(r["finish_position"]),
            "recent_form": round(form_score(prior_positions), 3),
            "track_form": round(rate(prior, lambda x: x.get("track","") == target_track), 3),
            "distance_form": round(rate(prior, lambda x: abs(num(x.get("distance")) - target_distance) <= 200), 3),
            "jockey_form": round(rate(prior, lambda x: x.get("jockey","").strip() == jockey), 3),
            "trainer_form": round(rate(prior, lambda x: x.get("trainer","").strip() == trainer), 3),
            "weight_score": round(max(0.0, min(100.0, 100.0 - abs(weight - 60.0) * 4)), 3),
            "agf_score": num(r.get("agf_score")),
            "history_count": len(prior),
            "hp": num(r.get("hp")),
        })

    path = DATA / "training.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        fields = ["race_id","race_date","finish_position",*FEATURES]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"training.csv: {len(rows)} rows / {len(set(r['race_id'] for r in rows))} races / {len(set(r['race_date'] for r in rows))} dates")

if __name__ == "__main__":
    build()
