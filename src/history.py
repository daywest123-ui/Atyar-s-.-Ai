from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
HISTORY_FILE = DATA / "historical_results.csv"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _position(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def _norm(value: Any) -> str:
    value = str(value or "").strip().casefold()
    table = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c"})
    value = value.translate(table)
    return re.sub(r"[^a-z0-9]+", "", value)


def _same_track(left: Any, right: Any) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and (a == b or a in b or b in a))


def _load_history_index() -> dict[str, list[dict]]:
    """Read verified past results already collected from the TJK archive.

    Avoids the retired/blocked third-party Nal Sesleri endpoint, which returned
    HTTP 406 for every horse and created noisy daily logs.
    """
    index: dict[str, list[dict]] = defaultdict(list)
    if not HISTORY_FILE.exists():
        return index

    with HISTORY_FILE.open("r", newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            horse = str(raw.get("horse") or "").strip()
            race_date = str(raw.get("race_date") or "").strip()
            finish = _position(raw.get("finish_position"))
            if not horse or not race_date or finish is None:
                continue

            index[_norm(horse)].append({
                "race_date": race_date,
                "race_number": raw.get("race_number", ""),
                "horse_name": horse,
                "result": str(finish),
                "hippodrome_id": raw.get("track", ""),
                "distance": _num(raw.get("distance")) or 0.0,
                "jockey": str(raw.get("jockey") or "").strip(),
                "trainer": str(raw.get("trainer") or "").strip(),
                "weight": _num(raw.get("weight")) or 0.0,
                "hp": _num(raw.get("hp")) or 0.0,
                "AGF1": _num(raw.get("agf_score")) or 0.0,
                "odds": _num(raw.get("odds")) or 0.0,
            })

    for rows in index.values():
        rows.sort(key=lambda row: row["race_date"], reverse=True)
    return index


def _rate(rows: list[dict], predicate) -> float:
    selected = [row for row in rows if predicate(row)]
    scores = []
    for row in selected:
        position = _position(row.get("result"))
        if position is not None:
            scores.append(max(0.0, 100.0 - (position - 1) * 18.0))
    return round(sum(scores) / len(scores), 2) if scores else 50.0


def summarize_history(
    rows: list[dict],
    target_track: str = "",
    target_distance: float = 0.0,
    target_jockey: str = "",
    target_trainer: str = "",
) -> dict:
    if not rows:
        return {
            "recent_form": 0.0, "track_form": 50.0, "distance_form": 50.0,
            "jockey_form": 50.0, "trainer_form": 50.0, "history_count": 0,
        }

    recent_scores = []
    for row in rows[:8]:
        position = _position(row.get("result"))
        if position is not None:
            recent_scores.append(max(0.0, 100.0 - (position - 1) * 15.0))

    track_form = (
        _rate(rows, lambda row: _same_track(row.get("hippodrome_id"), target_track))
        if target_track else 50.0
    )
    distance_form = (
        _rate(rows, lambda row: abs((_num(row.get("distance")) or 0.0) - target_distance) <= 200)
        if target_distance else 50.0
    )
    jockey_form = (
        _rate(rows, lambda row: _norm(row.get("jockey")) == _norm(target_jockey))
        if target_jockey else 50.0
    )
    trainer_form = (
        _rate(rows, lambda row: _norm(row.get("trainer")) == _norm(target_trainer))
        if target_trainer else 50.0
    )

    return {
        "recent_form": round(sum(recent_scores) / len(recent_scores), 2) if recent_scores else 0.0,
        "track_form": track_form,
        "distance_form": distance_form,
        "jockey_form": jockey_form,
        "trainer_form": trainer_form,
        "history_count": len(rows),
    }


def enrich_horses(horses: list[dict]) -> list[dict]:
    history_index = _load_history_index()

    for horse in horses:
        name = str(horse.get("horse") or "").strip()
        if not name:
            continue

        target_date = str(horse.get("date") or "")
        # Strictly exclude same-day results to prevent target-race leakage.
        prior = [
            row for row in history_index.get(_norm(name), [])
            if not target_date or row["race_date"] < target_date
        ]
        prior.sort(key=lambda row: row["race_date"], reverse=True)

        if prior:
            summary = summarize_history(
                prior,
                str(horse.get("track") or horse.get("hippodrome", "")),
                _num(horse.get("distance")) or 0.0,
                str(horse.get("jockey") or ""),
                str(horse.get("trainer") or ""),
            )
            horse.update(summary)
            horse["history_source_status"] = "local_tjk_archive"
        else:
            # Keep current TJK/PDF features when no verified prior result exists.
            horse["history_count"] = 0
            horse["history_source_status"] = "no_prior_records"

        horse["history_sample"] = prior[:20]

    return horses
