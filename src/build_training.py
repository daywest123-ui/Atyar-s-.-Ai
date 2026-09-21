from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

FIELDS = [
    "race_id", "race_date", "race_number", "track", "distance",
    "horse", "finish_position", "jockey", "trainer", "weight", "hp",
    "agf_score", "odds",
]

FEATURES = [
    "recent_form", "track_form", "distance_form",
    "jockey_form", "trainer_form", "weight_score", "agf_score", "history_count", "hp",
]

TURKISH_TRACKS = {
    "Adana Yeşiloba Hipodromu",
    "Ankara 75. Yıl Hipodromu",
    "Antalya Hipodromu",
    "Bursa Osmangazi Hipodromu",
    "Diyarbakır Hipodromu",
    "Elazığ Hipodromu",
    "İstanbul Veliefendi Hipodromu",
    "İzmir Şirinyer Hipodromu",
    "Kocaeli Kartepe Hipodromu",
    "Şanlıurfa Hipodromu",
}
# Some sources shorten track names. Keep matching conservative and explicit.
TURKISH_TRACK_TOKENS = (
    "adana", "ankara", "antalya", "bursa", "diyarbakır", "diyarbakir",
    "elazığ", "elazig", "istanbul", "izmir", "kocaeli", "şanlıurfa", "sanliurfa",
)

def num(v):
    try:
        return float(str(v).replace("%", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0

def clean_track(v: str) -> str:
    return " ".join(str(v or "").strip().split())

def is_turkish_track(track: str) -> bool:
    t = clean_track(track)
    if t in TURKISH_TRACKS:
        return True
    low = t.casefold()
    return any(tok in low for tok in TURKISH_TRACK_TOKENS)

def valid_result(r):
    try:
        finish = int(float(str(r.get("finish_position", "")).strip()))
        race_number = int(float(str(r.get("race_number", "")).strip()))
        distance = num(r.get("distance"))
    except (TypeError, ValueError):
        return False
    horse = str(r.get("horse", "")).strip()
    return (
        bool(horse)
        and finish >= 1
        and race_number >= 1
        and race_number <= 20
        and distance >= 1000
        and is_turkish_track(r.get("track", ""))
    )

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
        raise SystemExit("historical_results.csv yok; önce local backfill çalıştırılmalı.")

    raw_all = list(csv.DictReader(source.open("r", newline="", encoding="utf-8")))
    raw = [r for r in raw_all if valid_result(r)]

    if len(raw) < 100:
        raise SystemExit(
            f"Temiz Türk yarış verisi yetersiz: {len(raw)} satır. "
            "Karışık/yabancı veya eksik kayıtlarla model eğitimi engellendi."
        )

    raw.sort(key=lambda r: (
        r.get("race_date", ""), r.get("track", ""),
        int(float(r.get("race_number", 0) or 0)), r.get("horse", "")
    ))

    # Keep only complete race fields and deduplicate horse within a race.
    dedup = {}
    for r in raw:
        key = (r.get("race_id", "").strip(), r.get("horse", "").strip().upper())
        dedup[key] = r
    raw = list(dedup.values())

    by_horse = defaultdict(list)
    for r in raw:
        by_horse[r["horse"].strip().upper()].append(r)

    rows = []
    for r in raw:
        name = r["horse"].strip().upper()
        # Only records strictly before the target race count as prior evidence.
        # Same-day earlier races are intentionally excluded because ordering/time
        # is not reliably encoded in this dataset.
        prior = [x for x in by_horse[name] if x["race_date"] < r["race_date"]]
        target_track = r.get("track", "")
        target_distance = num(r.get("distance"))
        prior_positions = [int(x["finish_position"]) for x in prior]
        weight = num(r.get("weight")) or 60.0
        jockey = r.get("jockey", "").strip()
        trainer = r.get("trainer", "").strip()

        rows.append({
            "race_id": r["race_id"],
            "race_date": r["race_date"],
            "race_number": int(float(r["race_number"])),
            "track": target_track,
            "distance": target_distance,
            "horse": r["horse"],
            "jockey": jockey,
            "trainer": trainer,
            "weight": weight,
            "finish_position": int(float(r["finish_position"])),
            "recent_form": round(form_score(prior_positions), 3),
            "track_form": round(rate(prior, lambda x: x.get("track", "") == target_track), 3),
            "distance_form": round(rate(prior, lambda x: abs(num(x.get("distance")) - target_distance) <= 200), 3),
            "jockey_form": round(rate(prior, lambda x: x.get("jockey", "").strip() == jockey), 3),
            "trainer_form": round(rate(prior, lambda x: x.get("trainer", "").strip() == trainer), 3),
            "weight_score": round(max(0.0, min(100.0, 100.0 - abs(weight - 60.0) * 4)), 3),
            "agf_score": num(r.get("agf_score")),
            "history_count": len(prior),
            "hp": num(r.get("hp")),
        })

    races = {r["race_id"] for r in rows}
    dates = {r["race_date"] for r in rows}
    if len(races) < 20:
        raise SystemExit(f"Temiz eğitim verisi en az 20 yarış gerektirir; bulunan: {len(races)}")

    path = DATA / "training.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        fields = [
            "race_id", "race_date", "race_number", "track", "distance",
            "horse", "jockey", "trainer", "weight", "finish_position", *FEATURES
        ]
        w.writerows(rows)

    print(
        f"training.csv: {len(rows)} rows / {len(races)} races / {len(dates)} dates "
        f"(raw={len(raw_all)}, Turkish-valid={len(raw)})"
    )

if __name__ == "__main__":
    build()
