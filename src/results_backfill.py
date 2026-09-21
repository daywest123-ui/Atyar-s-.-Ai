from __future__ import annotations

import csv
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

# Primary historical source. This is the TJK e-Bayi JSON archive used by
# existing public TJK integrations; it is separate from the blocked web host.
EBAYI = "https://ebayi.tjk.org/s/d"
TJK = "https://www.tjk.org"
RESULTS_CITY = "/TR/YarisSever/Info/Sehir/GunlukYarisSonuclari"
RESULTS_DATA = "/TR/YarisSever/Info/Data/GunlukYarisSonuclari"

DOMESTIC = {
    1: "Adana", 2: "İzmir", 3: "İstanbul", 4: "Ankara", 5: "Bursa",
    6: "Elazığ", 7: "Diyarbakır", 8: "Şanlıurfa", 9: "Kocaeli", 10: "Antalya",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AtYarisiAI/5.0)",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

R = "gunluk-GunlukYarisSonuclari"
FIELDS = [
    "race_id", "race_date", "race_number", "track", "distance", "horse",
    "finish_position", "jockey", "trainer", "weight", "hp", "agf_score", "odds",
    "recent_form", "track_form", "distance_form", "jockey_form",
    "trainer_form", "weight_score", "model_probability", "history_count",
]


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def num(value):
    try:
        return float(str(value).replace("%", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def pos(value):
    m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", str(value or ""))
    return int(m.group()) if m else None


def clean_key(value):
    return re.sub(r"[^a-z0-9çğıöşü]", "", str(value).lower())


def pick(obj, aliases):
    if not isinstance(obj, dict):
        return None
    normalized = {clean_key(k): v for k, v in obj.items()}
    exact = {clean_key(a) for a in aliases}
    for key, value in normalized.items():
        if key in exact:
            return value
    for key, value in normalized.items():
        if any(clean_key(a) in key for a in aliases):
            return value
    return None


def scalar(value):
    return value if isinstance(value, (str, int, float)) else None


def walk_records(node, context=None):
    context = context or {}
    if isinstance(node, dict):
        local = dict(context)
        for field, aliases in {
            "race_number": ["RACE", "KOSU", "KOSUNO", "RACENO", "RACE_NO"],
            "track": ["KEY", "YER", "AD", "HIPODROM", "HIPODROMENAME"],
            "distance": ["MESAFE", "DISTANCE", "UZUNLUK"],
        }.items():
            value = scalar(pick(node, aliases))
            if value not in (None, ""):
                if field != "track" or len(str(value)) <= 40:
                    local[field] = value
        yield node, local
        for value in node.values():
            yield from walk_records(value, local)
    elif isinstance(node, list):
        for value in node:
            yield from walk_records(value, context)


def parse_ebayi_full(payload, d: date, track: str) -> list[dict]:
    out = []
    seen = set()

    for obj, context in walk_records(payload):
        horse = pick(obj, [
            "ATADI", "AT_ADI", "AT", "HORSE", "HORSE_NAME", "NAME",
            "ATADI3", "AtAdi", "AtAdi3",
        ])
        finish = pick(obj, [
            "SONUCNO", "SONUC", "SIRA", "SIRANO", "FINISH", "FINISHPOSITION",
            "RESULT", "DERECE_SIRA",
        ])
        if horse is None or finish is None:
            continue

        horse = str(horse).strip()
        finish_no = pos(finish)
        if not horse or finish_no is None:
            continue

        race_no = pos(
            pick(obj, ["KOSUNO", "RACENO", "RACE_NO", "RACE", "KOSU"])
            or context.get("race_number")
        )
        if race_no is None:
            continue

        distance = pos(
            pick(obj, ["MESAFE", "DISTANCE", "UZUNLUK"])
            or context.get("distance")
        ) or 0

        jockey = str(pick(obj, ["JOKEY", "JOKEYADI", "JOCKEY", "JOKEYAD"]) or "").strip()
        trainer = str(pick(obj, ["ANTRENOR", "ANTRENORADI", "TRAINER"]) or "").strip()
        weight = num(pick(obj, ["KILO", "WEIGHT"]))
        hp = num(pick(obj, ["HC", "HP", "HANDIKAPPUANI", "HANDIKAP"]))
        agf = num(pick(obj, ["AGF", "AGFORAN", "AGF_ORAN"]))
        odds = num(pick(obj, ["GNY", "GANYAN", "ODDS", "ORAN"]))

        key = (d.isoformat(), track, race_no, horse, finish_no)
        if key in seen:
            continue
        seen.add(key)

        out.append({
            "race_id": f"{d.isoformat()}-{track}-{race_no}",
            "race_date": d.isoformat(),
            "race_number": race_no,
            "track": track,
            "distance": distance,
            "horse": horse,
            "finish_position": finish_no,
            "jockey": jockey,
            "trainer": trainer,
            "weight": weight,
            "hp": hp,
            "agf_score": min(agf, 100.0),
            "odds": odds,
        })

    return out


def fetch_ebayi_day(d: date) -> list[dict]:
    ds = d.strftime("%Y%m%d")
    session = requests.Session()
    session.headers.update(HEADERS)

    index_url = f"{EBAYI}/sonuclar/{ds}/yarislar.json"
    response = session.get(index_url, timeout=(8, 25))
    response.raise_for_status()
    index = response.json()

    items = index.get("data", index) if isinstance(index, dict) else index
    if not isinstance(items, list):
        raise RuntimeError(f"e-Bayi index beklenmeyen JSON tipi: {type(items).__name__}")

    tracks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = pick(item, ["KEY", "key", "KOD", "CODE"])
        name = pick(item, ["AD", "ad", "YER", "name"])
        if key is not None:
            tracks.append((str(key), str(name or key)))

    if not tracks:
        raise RuntimeError("e-Bayi yarislar.json şehir listesi boş.")

    rows = []
    for key, name in tracks:
        try:
            r = session.get(
                f"{EBAYI}/sonuclar/{ds}/full/{key}.json",
                timeout=(8, 25),
            )
            r.raise_for_status()
            parsed = parse_ebayi_full(r.json(), d, name)
            rows.extend(parsed)
            if parsed:
                print(f"[backfill] e-Bayi {d} {name}: {len(parsed)} rows")
        except Exception as exc:
            print(f"[backfill] e-Bayi {d} {name}: {exc}")

    return rows


def parse_tjk_html(html: str, d: date, track: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    panes = soup.select("div.races-panes > div") or soup.select("div.race-details")

    for pane in panes:
        detail = pane.select_one("div.race-details") or pane
        race_no = pos(text(detail.select_one("h3.race-no")))
        if race_no is None:
            continue
        config = text(detail.select_one("h3.race-config"))
        dm = re.search(r"(\d{3,4})\s*m", config)
        distance = int(dm.group(1)) if dm else 0
        table = pane.select_one("table.tablesorter")
        if table is None:
            continue

        for tr in table.select("tbody tr"):
            cell = tr.select_one(f"td.{R}-AtAdi3")
            finish = pos(text(tr.select_one(f"td.{R}-SONUCNO")))
            if cell is None or finish is None:
                continue
            horse = re.sub(r"^\s*\(?\d+\)?\s*", "", text(cell)).strip()
            if not horse:
                continue
            out.append({
                "race_id": f"{d.isoformat()}-{track}-{race_no}",
                "race_date": d.isoformat(),
                "race_number": race_no,
                "track": track,
                "distance": distance,
                "horse": horse,
                "finish_position": finish,
                "jockey": text(tr.select_one(f"td.{R}-JokeAdi")),
                "trainer": text(tr.select_one(f"td.{R}-AntronorAdi")),
                "weight": num(text(tr.select_one(f"td.{R}-Kilo"))),
                "hp": num(text(tr.select_one(f"td.{R}-Hc"))),
                "agf_score": min(num(text(tr.select_one(f"td.{R}-AGFORAN"))), 100.0),
                "odds": num(text(tr.select_one(f"td.{R}-Gny"))),
            })
    return out


def fetch_tjk_city(sid: int, name: str, d: date) -> list[dict]:
    ds = d.strftime("%d.%m.%Y")
    for era in ("yesterday", "past"):
        try:
            r = requests.get(
                f"{TJK}{RESULTS_CITY}",
                params={"Era": era, "SehirId": sid, "QueryParameter_Tarih": ds, "SehirAdi": name},
                headers=HEADERS,
                timeout=(5, 12),
            )
            r.raise_for_status()
            rows = parse_tjk_html(r.text, d, name)
            if rows:
                return rows
            r = requests.get(
                f"{TJK}{RESULTS_DATA}",
                params={"Era": era, "SehirId": sid, "QueryParameter_Tarih": ds, "SehirAdi": name},
                headers=HEADERS,
                timeout=(5, 12),
            )
            r.raise_for_status()
            rows = parse_tjk_html(r.text, d, name)
            if rows:
                return rows
        except Exception as exc:
            print(f"[backfill] TJK {d} {name} {era}: {exc}")
    return []


def load_existing(path: Path) -> dict[tuple[str, str], dict]:
    existing = {}
    if not path.exists():
        return existing
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            existing[(row.get("race_id"), row.get("horse"))] = row
    return existing


def save_rows(path: Path, rows: dict[tuple[str, str], dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows.values())


def main():
    days = max(1, min(int(os.environ.get("BACKFILL_DAYS", "7")), 7))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    dates = [start + timedelta(days=i) for i in range(days)]
    path = DATA / "historical_results.csv"
    existing = load_existing(path)

    for d in dates:
        rows = []
        try:
            rows = fetch_ebayi_day(d)
            print(f"[backfill] e-Bayi day {d}: {len(rows)} rows / "
                  f"{len({r['race_id'] for r in rows})} races")
        except Exception as exc:
            print(f"[backfill] e-Bayi failed {d}: {exc}")

        if not rows:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [
                    pool.submit(fetch_tjk_city, sid, name, d)
                    for sid, name in DOMESTIC.items()
                ]
                for future in as_completed(futures):
                    try:
                        rows.extend(future.result())
                    except Exception as exc:
                        print(f"[backfill] TJK task failed {d}: {exc}")

        for row in rows:
            existing[(row["race_id"], row["horse"])] = row

        save_rows(path, existing)
        print(
            f"[backfill] checkpoint {d}: rows={len(existing)} "
            f"races={len({key[0] for key in existing})} "
            f"dates={len({r.get('race_date') for r in existing.values()})}"
        )

    print(
        f"[backfill] finished: rows={len(existing)} "
        f"races={len({key[0] for key in existing})} "
        f"dates={len({r.get('race_date') for r in existing.values()})}"
    )


if __name__ == "__main__":
    main()
