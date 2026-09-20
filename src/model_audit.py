from __future__ import annotations

"""Pre-run audit for historical training data.

This is deliberately diagnostic: it never changes the model or claims
profitability. It checks chronology, class balance, sample depth and whether
the training table is large enough for the learned model.
"""

import json
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"


def main() -> dict:
    path = DATA / "training.csv"
    report = {
        "status": "low_data",
        "rows": 0,
        "races": 0,
        "winners": 0,
        "date_min": None,
        "date_max": None,
        "feature_missing_rate": {},
        "warnings": [],
    }
    if not path.exists():
        report["warnings"].append("training.csv yok")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report

    df = pd.read_csv(path)
    report["rows"] = int(len(df))
    report["races"] = int(df["race_id"].nunique()) if "race_id" in df else 0
    report["winners"] = int((pd.to_numeric(df["finish_position"], errors="coerce") == 1).sum()) if "finish_position" in df else 0
    if "race_date" in df:
        dates = pd.to_datetime(df["race_date"], errors="coerce").dropna()
        if not dates.empty:
            report["date_min"] = dates.min().date().isoformat()
            report["date_max"] = dates.max().date().isoformat()
            if dates.is_monotonic_increasing is False:
                report["warnings"].append("training.csv tarih sırası karışık; model kronolojik olarak sıralıyor")

    for col in df.columns:
        report["feature_missing_rate"][col] = round(float(df[col].isna().mean()), 4)

    if report["races"] < 20:
        report["warnings"].append("20'den az tarihsel yarış: öğrenilmiş model güvenilmez olabilir")
    if report["races"] >= 100 and report["winners"] >= 20:
        report["status"] = "usable"
    elif report["races"] >= 20:
        report["status"] = "limited"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    main()
