from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

FEATURES = [
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score", "agf_score",
]


def _num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _pos(v):
    try:
        return int(str(v).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _rows_from_json(path: Path):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("horses", "results", "data"):
            if isinstance(obj.get(key), list):
                return obj[key]
    return []


def normalize(row, race_id):
    # Accept several common field names so archived TJK/Nal Sesleri JSON
    # can be converted without rewriting the collector.
    pos = _pos(row.get("finish_position", row.get("result", row.get("sira"))))
    if pos is None:
        return None
    out = {
        "race_id": race_id,
        "finish_position": pos,
    }
    aliases = {
        "recent_form": ("recent_form", "form_score"),
        "track_form": ("track_form",),
        "distance_form": ("distance_form",),
        "jockey_form": ("jockey_form",),
        "trainer_form": ("trainer_form",),
        "weight_score": ("weight_score",),
        "agf_score": ("agf_score", "agf"),
    }
    for feature, keys in aliases.items():
        value = next((_num(row.get(k)) for k in keys if row.get(k) is not None), None)
        out[feature] = 0.0 if value is None else value
    return out


def build():
    candidates = []
    for p in sorted(DATA.glob("*.json")):
        if p.name in {
            "horses.json", "raw_program.json",
            "ranked_horses.json", "advanced_ranked_horses.json",
            "race_analysis.json",
        }:
            continue
        candidates.append(p)

    rows = []
    for p in candidates:
        source = _rows_from_json(p)
        for i, raw in enumerate(source):
            race_id = raw.get("race_id") or raw.get("race") or f"{p.stem}-{i}"
            item = normalize(raw, str(race_id))
            if item:
                rows.append(item)

    if not rows:
        print("No archived result JSON found. training.csv was not changed.")
        return

    path = DATA / "training.csv"
    existing = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (r.get("race_id"), r.get("finish_position"))
                existing[key] = r

    for r in rows:
        existing[(r["race_id"], r["finish_position"])] = r

    with path.open("w", newline="", encoding="utf-8") as f:
        fields = ["race_id", "finish_position", *FEATURES]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(existing.values())

    print(f"training.csv: {len(existing)} rows")


if __name__ == "__main__":
    build()
