from __future__ import annotations

"""Leakage-aware lightweight backtest utilities for archived race rows."""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"


def evaluate_archives() -> dict:
    files = sorted(DATA.glob("*.json"))
    races = 0
    races_with_top1 = 0
    rows = 0

    for path in files:
        if path.name in {
            "horses.json", "raw_program.json", "advanced_ranked_horses.json",
            "ranked_horses.json", "race_analysis.json", "sixli_coupons.json",
        }:
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data = obj if isinstance(obj, list) else obj.get("horses", obj.get("results", []))
        if not isinstance(data, list):
            continue

        grouped = {}
        for row in data:
            race = str(row.get("race") or row.get("race_id") or "")
            if not race:
                continue
            grouped.setdefault(race, []).append(row)
            rows += 1

        for group in grouped.values():
            finished = [r for r in group if str(r.get("finish_position", r.get("result", ""))).split()[0] == "1"]
            if not finished:
                continue
            races += 1
            best = max(group, key=lambda r: float(r.get("model_probability", 0) or 0))
            if best in finished:
                races_with_top1 += 1

    return {
        "archived_rows": rows,
        "evaluated_races": races,
        "top1_hit_rate": (races_with_top1 / races) if races else None,
        "note": "This is a diagnostic metric, not a guarantee of future performance.",
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_archives(), ensure_ascii=False, indent=2))
