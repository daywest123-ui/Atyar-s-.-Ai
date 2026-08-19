from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

import requests

BASE_URL = "https://nalsesleri.com/api"


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "."))
    except ValueError:
        return None


def _position(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"\d+", str(value))
    return int(m.group()) if m else None


def fetch_history(horse_name: str) -> list[dict]:
    r = requests.get(BASE_URL, params={"horse_name": horse_name}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    if payload.get("status") != "success":
        return []
    return payload.get("data") or []


def _rate(rows: list[dict], predicate) -> float:
    selected = [r for r in rows if predicate(r)]
    if not selected:
        return 50.0
    scored = []
    for r in selected:
        p = _position(r.get("result"))
        if p is not None:
            scored.append(max(0.0, 100.0 - (p - 1) * 18.0))
    return round(sum(scored) / len(scored), 2) if scored else 50.0


def summarize_history(rows: list[dict], target_track: str = "", target_distance: float = 0.0) -> dict:
    if not rows:
        return {"recent_form": 0.0, "track_form": 50.0, "distance_form": 50.0,
                "jockey_form": 50.0, "trainer_form": 50.0, "history_count": 0}

    recent = rows[:8]
    recent_scores = []
    for r in recent:
        p = _position(r.get("result"))
        if p is not None:
            recent_scores.append(max(0.0, 100.0 - (p - 1) * 15.0))

    track = _rate(rows, lambda r: str(r.get("hippodrome", r.get("hippodrome_id", ""))).lower() == str(target_track).lower()) if target_track else 50.0
    distance = _rate(rows, lambda r: abs((_num(r.get("distance")) or 0) - target_distance) <= 200) if target_distance else 50.0

    jockeys = defaultdict(list)
    trainers = defaultdict(list)
    for r in rows:
        p = _position(r.get("result"))
        if p is None:
            continue
        score = max(0.0, 100.0 - (p - 1) * 18.0)
        if r.get("jockey"):
            jockeys[str(r["jockey"]).strip()].append(score)
        if r.get("trainer") or r.get("antrenor"):
            key = str(r.get("trainer") or r.get("antrenor")).strip()
            trainers[key].append(score)

    def best(mapping):
        vals = [sum(v) / len(v) for v in mapping.values() if v]
        return round(max(vals), 2) if vals else 50.0

    return {
        "recent_form": round(sum(recent_scores) / len(recent_scores), 2) if recent_scores else 0.0,
        "track_form": track,
        "distance_form": distance,
        "jockey_form": best(jockeys),
        "trainer_form": best(trainers),
        "history_count": len(rows),
    }


def enrich_horses(horses: list[dict]) -> list[dict]:
    cache: dict[str, list[dict]] = {}
    for horse in horses:
        name = horse.get("horse", "").strip()
        if not name:
            continue
        try:
            cache[name.lower()] = fetch_history(name)
        except requests.RequestException as exc:
            print(f"history fetch failed for {name}: {exc}")
            cache[name.lower()] = []

        history = cache[name.lower()]
        target_distance = _num(horse.get("distance")) or 0.0
        summary = summarize_history(history, str(horse.get("hippodrome", "")), target_distance)
        horse.update(summary)
        horse["history_sample"] = history[:20]
    return horses
