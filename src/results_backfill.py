from __future__ import annotations

import csv
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

TJK = "https://www.tjk.org"
RESULTS_CITY = "/TR/YarisSever/Info/Sehir/GunlukYarisSonuclari"
DOMESTIC = {
    1: "Adana", 2: "İzmir", 3: "Ankara", 4: "Bursa", 5: "İstanbul",
    6: "Şanlıurfa", 7: "Elazığ", 8: "Kocaeli", 9: "Diyarbakır", 10: "Antalya",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AtYarisiAI/4.0)",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}
R = "gunluk-GunlukYarisSonuclari"
FIELDS = [
    "race_id", "race_date", "race_number", "track", "distance", "horse",
    "finish_position", "jockey", "trainer", "weight", "hp", "agf_score", "odds",
]


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def num(value):
    try:
        return float(str(value).replace("%", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return 0.0


def pos(value):
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else None


def parse_city(html: str, d: date, track: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    panes = soup.select("div.races-panes > div") or soup.select("div.race-details")

    for pane in panes:
        detail = pane.select_one("div.race-details") or pane
        race_text = text(detail.select_one("h3.race-no"))
        match = re.search(r"(\d+)\.?\s*Koşu", race_text, re.I)
        race_no = int(match.group(1)) if match else pos(race_text)
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
            if cell is None:
                continue
            horse = re.sub(r"^\s*\(?\d+\)?\s*", "", text(cell)).strip()
            horse = re.sub(r"\s+\(\d+\)\s*$", "", horse).strip()
            finish = pos(text(tr.select_one(f"td.{R}-SONUCNO")))
            if not horse or finish is None:
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


def fetch_city(sid: int, name: str, d: date) -> list[dict]:
    ds = d.strftime("%d.%m.%Y")
    for era in ("yesterday", "past"):
        for attempt in range(2):
            try:
                with requests.Session() as session:
                    session.headers.update(HEADERS)
                    response = session.get(
                        f"{TJK}{RESULTS_CITY}",
                        params={
                            "Era": era,
                            "SehirId": str(sid),
                            "QueryParameter_Tarih": ds,
                            "SehirAdi": name,
                        },
                        timeout=(5, 12),
                    )
                    response.raise_for_status()
                    rows = parse_city(response.text, d, name)
                    if rows:
                        return rows
            except Exception as exc:
                print(f"[backfill] {d} {name} era={era} attempt={attempt + 1}: {exc}")
                time.sleep(0.4 * (attempt + 1))
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

    # Short, bounded batches: a slow TJK day can never hold the whole workflow hostage.
    for d in dates:
        tasks = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            for sid, name in DOMESTIC.items():
                tasks.append(pool.submit(fetch_city, sid, name, d))
            for future in as_completed(tasks):
                try:
                    rows = future.result()
                    for row in rows:
                        existing[(row["race_id"], row["horse"])] = row
                except Exception as exc:
                    print(f"[backfill] {d} city task failed: {exc}")

        save_rows(path, existing)
        race_count = len({key[0] for key in existing})
        print(
            f"[backfill] checkpoint {d}: rows={len(existing)} "
            f"races={race_count} dates={len({r.get('race_date') for r in existing.values()})}"
        )

    race_count = len({key[0] for key in existing})
    print(
        f"[backfill] finished: rows={len(existing)} races={race_count} "
        f"dates={len({r.get('race_date') for r in existing.values()})}"
    )

    # Do not fail the entire daily pipeline because TJK omitted a historical day.
    # Model audit will report whether the accumulated training set is sufficient.


if __name__ == "__main__":
    main()
