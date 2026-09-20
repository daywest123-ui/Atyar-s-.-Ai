from __future__ import annotations

import csv
import os
import re
import time
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

TJK = "https://www.tjk.org"
GANYAN = "https://ganyan.app"
JINA = "https://r.jina.ai/"
RESULTS_CITY = "/TR/YarisSever/Info/Sehir/GunlukYarisSonuclari"
DOMESTIC = {
    1: "Adana", 2: "İzmir", 3: "İstanbul", 4: "Ankara", 5: "Bursa",
    6: "Elazığ", 7: "Diyarbakır", 8: "Şanlıurfa", 9: "Kocaeli", 10: "Antalya",
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



def fetch_ganyan_day(d: date) -> list[dict]:
    """Fallback source when TJK is unreachable from GitHub Actions."""
    try:
        session=requests.Session(); session.headers.update(HEADERS)
        r=session.get(f"{GANYAN}/?karma={d.isoformat()}",timeout=(5,15)); r.raise_for_status()
        soup=BeautifulSoup(r.text,"html.parser"); links=[]; seen=set()
        for a in soup.select('a[href*="/kosu/"]'):
            href=urljoin(GANYAN,a.get("href",""))
            if href not in seen: seen.add(href); links.append(href)
        out=[]
        for href in links[:80]:
            rr=session.get(href,timeout=(5,12)); rr.raise_for_status(); rs=BeautifulSoup(rr.text,"html.parser")
            title=text(rs.select_one("h1")) or text(rs.select_one("h2")); m=re.search(r"(\d+)\.?\s*Koşu",title,re.I); rn=int(m.group(1)) if m else None
            if rn is None:
                sm=re.search(r"-(\\d+)-kosu",href); rn=int(sm.group(1)) if sm else None
            if rn is None: continue
            body=text(rs.select_one("body"))[:1600]; track=next((n for n in DOMESTIC.values() if n.lower() in body.lower()),"Ganyan")
            for tr in rs.select("table tr"):
                cells=[text(td) for td in tr.select("td")]
                if len(cells)<4: continue
                finish=pos(cells[0]); horse=cells[2].strip()
                if finish is None or not horse or horse.lower() in {"at","horse"}: continue
                nums=[]
                for v in cells[3:]:
                    n=num(v.replace(".","").replace(",", "."))
                    if n>0: nums.append(n)
                out.append({"race_id":f"{d.isoformat()}-{track}-{rn}","race_date":d.isoformat(),"race_number":rn,"track":track,"distance":0,"horse":horse,"finish_position":finish,"jockey":"","trainer":"","weight":0.0,"hp":0.0,"agf_score":min(next((n for n in nums[1:] if n<=100),0.0),100.0),"odds":nums[0] if nums else 0.0})
        return out
    except Exception as exc:
        print(f"[backfill] ganyan.app {d}: {exc}"); return []

def fetch_jina_city(sid: int, name: str, d: date) -> list[dict]:
    """Fetch the same TJK results page through Jina Reader when GitHub IPs are blocked."""
    try:
        ds = d.strftime("%d.%m.%Y")
        target = (f"{TJK}{RESULTS_CITY}?Era=yesterday&SehirId={sid}"
                  f"&QueryParameter_Tarih={ds}&SehirAdi={name}")
        r = requests.get(f"{JINA}{target}", headers={**HEADERS, "X-Engine": "browser", "X-Timeout": "20"}, timeout=(8, 28))
        r.raise_for_status()
        lines = [x.strip() for x in r.text.splitlines() if x.strip()]
        out = []; race_no = None; distance = 0
        for line in lines:
            m = re.search(r"(\d+)\.?\s*Koşu", line, re.I)
            if m:
                race_no = int(m.group(1)); dm = re.search(r"(\d{3,4})\s*m", line); distance = int(dm.group(1)) if dm else 0
            if not line.startswith("|") or line.count("|") < 4 or race_no is None: continue
            cells = [x.strip() for x in line.strip("|").split("|")]; low = [x.lower() for x in cells]
            if any("sonuç" in x or "sonuc" in x for x in low): continue
            if any(re.fullmatch(r":?-{3,}:?", x) for x in cells): continue
            finish = pos(cells[0]); horse_idx = next((i for i,x in enumerate(low) if "at adı" in x or x in {"at","horse"}), None)
            if horse_idx is None: horse_idx = 1 if len(cells) > 1 else None
            if finish is None or horse_idx is None: continue
            horse = re.sub(r"\(\d+\)", "", cells[horse_idx]).strip()
            if not horse or len(horse) < 2: continue
            out.append({"race_id": f"{d.isoformat()}-{name}-{race_no}", "race_date": d.isoformat(), "race_number": race_no, "track": name, "distance": distance, "horse": horse, "finish_position": finish, "jockey": "", "trainer": "", "weight": 0.0, "hp": 0.0, "agf_score": 0.0, "odds": 0.0})
        return out
    except Exception as exc:
        print(f"[backfill] jina {d} {name}: {exc}"); return []
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

        if not any(row.get("race_date") == d.isoformat() for row in existing.values()):
            proxy_rows = []
            with ThreadPoolExecutor(max_workers=5) as pool:
                proxy_tasks = [
                    pool.submit(fetch_jina_city, sid, name, d)
                    for sid, name in DOMESTIC.items()
                ]
                for future in as_completed(proxy_tasks):
                    try:
                        proxy_rows.extend(future.result())
                    except Exception as exc:
                        print(f"[backfill] {d} Jina task failed: {exc}")
            for row in proxy_rows:
                existing[(row["race_id"], row["horse"])] = row
            if proxy_rows:
                print(f"[backfill] TJK/Jina fallback {d}: +{len(proxy_rows)} rows")
            else:
                mirror_rows = fetch_ganyan_day(d)
                for row in mirror_rows:
                    existing[(row["race_id"], row["horse"])] = row
                if mirror_rows:
                    print(f"[backfill] Ganyan fallback {d}: +{len(mirror_rows)} rows")
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
