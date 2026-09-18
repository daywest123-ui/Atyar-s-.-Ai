from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
ARCHIVE = DATA / "archive"
ARCHIVE.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score", "agf_score",
]


def num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def pos(v):
    try:
        return int(str(v).strip().split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def performance(rows):
    vals = []
    for r in rows[:8]:
        p = pos(r.get("result"))
        if p is not None:
            vals.append(max(0.0, 100.0 - (p - 1) * 15.0))
    return sum(vals) / len(vals) if vals else 50.0


def filtered(rows, predicate):
    selected = [r for r in rows if predicate(r)]
    vals = []
    for r in selected:
        p = pos(r.get("result"))
        if p is not None:
            vals.append(max(0.0, 100.0 - (p - 1) * 18.0))
    return sum(vals) / len(vals) if vals else 50.0


def archive():
    source = DATA / "horses.json"
    if not source.exists():
        print("horses.json missing; run collector first.")
        return 0

    horses = json.loads(source.read_text(encoding="utf-8"))
    out = []
    seen = set()

    for horse in horses:
        history = horse.get("history_sample") or []
        history = sorted(history, key=lambda r: str(r.get("race_date", "")))
        for i, r in enumerate(history):
            race_date = str(r.get("race_date") or "")
            race_no = str(r.get("race_number") or "")
            name = str(r.get("horse_name") or horse.get("horse") or "")
            finish = pos(r.get("result"))
            if not race_date or finish is None or not name:
                continue

            key = (race_date, race_no, name)
            if key in seen:
                continue
            seen.add(key)

            prior = history[:i]
            target_track = str(r.get("hippodrome_id") or "")
            target_distance = num(r.get("distance")) or 0

            row = {
                "race_id": f"{race_date}-{race_no}-{target_track}",
                "race_date": race_date,
                "horse": name,
                "finish_position": finish,
                "recent_form": round(performance(prior), 3),
                "track_form": round(filtered(prior, lambda x: str(x.get("hippodrome_id") or "") == target_track), 3),
                "distance_form": round(filtered(prior, lambda x: abs((num(x.get("distance")) or 0) - target_distance) <= 200), 3),
                "jockey_form": round(filtered(prior, lambda x: str(x.get("jockey") or "") == str(r.get("jockey") or "")), 3),
                "trainer_form": 50.0,
                "weight_score": round(max(0.0, min(100.0, 100.0 - abs((num(r.get("weight")) or 60.0) - 60.0) * 4)), 3),
                "agf_score": round(num(r.get("AGF1") or r.get("agf")) or 0.0, 3),
                "odds": num(r.get("odds")),
            }
            out.append(row)

    path = ARCHIVE / f"results_{date.today().isoformat()}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # Also maintain a compact append-only CSV archive for reproducible training.
    csv_path = DATA / "historical_results.csv"
    existing = {}
    if csv_path.exists():
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[(row.get("race_id"), row.get("horse"))] = row

    for row in out:
        existing[(row["race_id"], row["horse"])] = row

    fields = ["race_id", "race_date", "horse", "finish_position", *FEATURES, "odds"]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(existing.values())

    print(f"Archived {len(out)} historical race rows; total archive={len(existing)}")
    return len(out)


if __name__ == "__main__":
    archive()
