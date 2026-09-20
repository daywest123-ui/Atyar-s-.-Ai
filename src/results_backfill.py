from __future__ import annotations

import csv
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

TJK = "https://www.tjk.org"
RESULTS_PAGE = "/TR/YarisSever/Info/Page/GunlukYarisSonuclari"
RESULTS_CITY = "/TR/YarisSever/Info/Sehir/GunlukYarisSonuclari"
DOMESTIC = {1,2,3,4,5,6,7,8,9,10}
CITY_NAMES = {1: "Adana", 2: "İzmir", 3: "Ankara", 4: "Bursa", 5: "İstanbul", 6: "Şanlıurfa", 7: "Elazığ", 8: "Kocaeli", 9: "Diyarbakır", 10: "Antalya"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AtYarisiAI/3.0)",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}
R = "gunluk-GunlukYarisSonuclari"

def text(node):
    return node.get_text(" ", strip=True) if node else ""

def num(v):
    try:
        return float(str(v).replace("%","").replace(",","." ).strip())
    except (TypeError, ValueError):
        return 0.0

def pos(v):
    m = re.search(r"\d+", str(v or ""))
    return int(m.group()) if m else None

def parse_day(session: requests.Session, d: date) -> list[dict]:
    """Fetch historical results directly from the TJK city endpoint."""
    ds = d.strftime("%d.%m.%Y")

    def city(sid: int, name: str):
        last = None
        for attempt in range(3):
            try:
                rr = session.get(
                    f"{TJK}{RESULTS_CITY}",
                    params={
                        "Era": "yesterday",
                        "SehirId": str(sid),
                        "QueryParameter_Tarih": ds,
                        "SehirAdi": name,
                    },
                    timeout=(8, 18),
                )
                rr.raise_for_status()
                return parse_city(rr.text, d, name)
            except Exception as exc:
                last = exc
                if attempt < 2:
                    import time
                    time.sleep(0.8 * (attempt + 1))
        raise last

    rows = []
    for sid in sorted(DOMESTIC):
        name = CITY_NAMES[sid]
        try:
            rows.extend(city(sid, name))
        except Exception as exc:
            print(f"[backfill] {d} {name} failed: {exc}")
    return rows

def parse_city(html: str, d: date, track: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    panes = soup.select("div.races-panes > div")
    if not panes:
        panes = soup.select("div.race-details")

    for pane in panes:
        detail = pane.select_one("div.race-details") or pane
        race_text = text(detail.select_one("h3.race-no"))
        m = re.search(r"(\d+)\.?\s*Koşu", race_text, re.I)
        race_no = int(m.group(1)) if m else pos(race_text)
        if race_no is None:
            continue
        config = text(detail.select_one("h3.race-config"))
        dm = re.search(r"(\d{3,4})\s*m", config)
        distance = int(dm.group(1)) if dm else 0
        table = pane.select_one("table.tablesorter")
        if table is None:
            continue
        for tr in table.select("tbody tr"):
            name_cell = tr.select_one(f"td.{R}-AtAdi3")
            if name_cell is None:
                continue
            raw_name = text(name_cell)
            horse = re.sub(r"^\s*\(?\d+\)?\s*", "", raw_name).strip()
            horse = re.sub(r"\s+\(\d+\)\s*$", "", horse).strip()
            if not horse:
                continue
            finish = pos(text(tr.select_one(f"td.{R}-SONUCNO")))
            if finish is None:
                continue
            jockey = text(tr.select_one(f"td.{R}-JokeAdi"))
            trainer = text(tr.select_one(f"td.{R}-AntronorAdi"))
            weight = num(text(tr.select_one(f"td.{R}-Kilo")))
            hp = num(text(tr.select_one(f"td.{R}-Hc")))
            agf = num(text(tr.select_one(f"td.{R}-AGFORAN")))
            gny = num(text(tr.select_one(f"td.{R}-Gny")))
            out.append({
                "race_id": f"{d.isoformat()}-{track}-{race_no}",
                "race_date": d.isoformat(),
                "race_number": race_no,
                "track": track,
                "distance": distance,
                "horse": horse,
                "finish_position": finish,
                "jockey": jockey,
                "trainer": trainer,
                "weight": weight,
                "hp": hp,
                "agf_score": min(agf, 100.0),
                "odds": gny,
            })
    return out

def main():
    days = int(__import__("os").environ.get("BACKFILL_DAYS", "45"))
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    dates = [start + timedelta(days=i) for i in range(days)]

    all_rows = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for d in dates:
            s = requests.Session()
            s.headers.update(HEADERS)
            futures[pool.submit(parse_day, s, d)] = d
        for f in as_completed(futures):
            d = futures[f]
            try:
                rows = f.result()
                print(f"[backfill] {d}: {len(rows)} horses")
                all_rows.extend(rows)
            except Exception as exc:
                print(f"[backfill] {d} FAILED: {exc}")

    path = DATA / "historical_results.csv"
    existing = {}
    if path.exists():
        with path.open("r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[(row.get("race_id"), row.get("horse"))] = row
    for row in all_rows:
        existing[(row["race_id"], row["horse"])] = row

    fields = [
        "race_id","race_date","race_number","track","distance","horse",
        "finish_position","jockey","trainer","weight","hp","agf_score","odds"
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(existing.values())

    race_count = len({r["race_id"] for r in existing.values()})
    print(f"[backfill] total rows={len(existing)} races={race_count} dates={len(set(r.get('race_date') for r in existing.values()))}")
    if race_count < 20:
        raise SystemExit("Historical backfill produced fewer than 20 races; refusing to train on insufficient data.")

if __name__ == "__main__":
    main()
