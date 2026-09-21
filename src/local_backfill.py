from __future__ import annotations

import csv, os, re
from datetime import date, timedelta
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "historical_results.csv"
BASE = "https://ebayi.tjk.org/s/d"
FIELDS = ["race_id","race_date","race_number","track","distance","horse","finish_position","jockey","trainer","weight","hp","agf_score","odds"]
H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://www.tjk.org/",
}

TURKISH_TRACK_TOKENS = (
    "adana", "ankara", "antalya", "bursa", "diyarbakır", "diyarbakir",
    "elazığ", "elazig", "istanbul", "istanbul", "izmir", "kocaeli",
    "şanlıurfa", "sanliurfa",
)

def norm(v):
    return re.sub(r"[^a-z0-9çğıöşü]", "", str(v).casefold())

def pick(o, names):
    if not isinstance(o, dict):
        return None
    ns = {norm(x) for x in names}
    for k, v in o.items():
        if norm(k) in ns:
            return v
    return None

def num(v):
    try:
        return float(str(v).replace("%","").replace(",","." ).strip())
    except (TypeError, ValueError):
        return 0.0

def integer(v):
    m = re.search(r"(?<!\d)(\d{1,3})(?!\d)", str(v or ""))
    return int(m.group(1)) if m else None

def is_turkish_track(name):
    low = str(name or "").casefold()
    return any(token in low for token in TURKISH_TRACK_TOKENS)

def is_time_like(v):
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", str(v or "").strip()))

def is_horse_name(v):
    s = str(v or "").strip()
    if not s or is_time_like(s):
        return False
    if len(s) < 2 or len(s) > 80:
        return False
    # Reject obvious numeric-only artifacts from the old parser.
    if re.fullmatch(r"[\d\s./:-]+", s):
        return False
    return True

def parse_distance(v):
    n = integer(v)
    return n if n and 800 <= n <= 5000 else 0

def extract_race_number(o):
    return integer(pick(o, ["KOSUNO","KOSU_NO","RACENO","RACE_NO","RACE","KOSU","KOSUSIRASI"]))

def extract_finish(o):
    return integer(pick(o, ["SONUCNO","SONUC","SIRANO","SIRA","FINISH","FINISHPOSITION","RESULT"]))

def unwrap_data(payload):
    # e-Bayi responses are commonly wrapped as {"status": ..., "data": ...}.
    # Keep accepting bare lists/dicts because mirrors may return the payload directly.
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload

def parse_full(payload, day, track):
    """
    Parse e-Bayi's nested result JSON with inherited race context.
    Race metadata and horse/result metadata may live at different nesting levels.
    """
    out, seen = [], set()
    payload = unwrap_data(payload)

    def visit(node, inherited_race=None, inherited_distance=0):
        if isinstance(node, dict):
            own_race = extract_race_number(node) or inherited_race
            own_distance = parse_distance(pick(node, ["MESAFE","DISTANCE","UZUNLUK"])) or inherited_distance

            horse = pick(node, [
                "ATADI", "AT_ADI", "AT", "HORSE", "HORSE_NAME",
                "ATADI1", "ATADI2", "ATADI3", "ATADI4", "ATADI5",
                "ATADI6", "ATADI7", "ATADI8", "ATADI9", "ATADI10",
                "ATADI11", "ATADI12", "ADI"
            ])
            fin = extract_finish(node)

            if is_horse_name(horse) and own_race and fin and 1 <= fin <= 30:
                horse = str(horse).strip()
                key = (day, track, own_race, horse.casefold())
                if key not in seen:
                    seen.add(key)
                    out.append({
                        "race_id": f"{day}-{track}-{own_race}",
                        "race_date": day,
                        "race_number": own_race,
                        "track": track,
                        "distance": own_distance,
                        "horse": horse,
                        "finish_position": fin,
                        "jockey": str(pick(node, ["JOKEY","JOKEYADI","JOCKEY","JOKEYAD"]) or "").strip(),
                        "trainer": str(pick(node, ["ANTRENOR","ANTRENORADI","TRAINER"]) or "").strip(),
                        "weight": num(pick(node, ["KILO","WEIGHT"])),
                        "hp": num(pick(node, ["HC","HP","HANDIKAPPUANI","HANDIKAP"])),
                        "agf_score": min(num(pick(node, ["AGF","AGFORAN","AGF_ORAN"])), 100),
                        "odds": num(pick(node, ["GNY","GANYAN","ODDS","ORAN"])),
                    })

            for v in node.values():
                visit(v, own_race, own_distance)

        elif isinstance(node, list):
            for v in node:
                visit(v, inherited_race, inherited_distance)

    visit(payload)
    return out

def get(url):
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=H, timeout=(10, 30))
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            if attempt < 2:
                import time
                time.sleep(1.5 * (attempt + 1))
    raise last

def day_rows(day):
    ds = day.replace("-", "")
    idx = get(f"{BASE}/sonuclar/{ds}/yarislar.json")
    items = unwrap_data(idx)
    tracks = []

    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict):
            continue
        key = pick(x, ["KEY","KOD","CODE"])
        name = pick(x, ["AD","YER","NAME"]) or key
        if key and name and is_turkish_track(name):
            tracks.append((str(key), str(name)))

    rows = []
    for key, name in tracks:
        try:
            payload = get(f"{BASE}/sonuclar/{ds}/full/{key}.json")
            got = parse_full(payload, day, name)
            rows.extend(got)
            races = len({r["race_id"] for r in got})
            print(f"[local-backfill] {day} {name}: {len(got)} rows / {races} races")
        except Exception as e:
            print(f"[local-backfill] {day} {name}: {e}")

    return rows

def main():
    days = max(1, min(int(os.getenv("BACKFILL_DAYS", "30")), 90))
    end = date.today() - timedelta(days=1)
    dates = [end - timedelta(days=i) for i in range(days - 1, -1, -1)]

    DATA.mkdir(exist_ok=True)
    existing = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if is_turkish_track(r.get("track","")) and not is_time_like(r.get("horse","")):
                    existing[(r.get("race_id"), r.get("horse","").strip().casefold())] = r

    total_new = 0
    for d in dates:
        ds = d.isoformat()
        try:
            rows = day_rows(ds)
        except Exception as e:
            print(f"[local-backfill] DAY FAILED {ds}: {e}")
            continue

        for r in rows:
            existing[(r["race_id"], r["horse"].casefold())] = r
        total_new += len(rows)

        with OUT.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(sorted(existing.values(), key=lambda x: (x["race_date"], x["track"], int(x["race_number"]), x["horse"])))

        print(f"[local-backfill] checkpoint {ds}: {len(existing)} valid Turkish rows")

    races = {r["race_id"] for r in existing.values()}
    print(f"[local-backfill] DONE: {len(existing)} rows / {len(races)} races")

    if not existing:
        raise SystemExit("Yerel TJK e-Bayi bağlantısı geçerli Türk yarış verisi döndürmedi.")

if __name__ == "__main__":
    main()
