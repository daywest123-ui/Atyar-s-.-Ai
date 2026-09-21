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
    return integer(pick(o, [
        "KOSUNO","KOSU_NO","RACENO","RACE_NO","RACE","KOSU","KOSUSIRASI",
        "KOSUNOSU","KOSUSIRASI","NO","SIRAID","SIRA_ID"
    ]))

def extract_finish(o):
    return integer(pick(o, [
        "SONUCNO","SONUC","SIRANO","SIRA","FINISH","FINISHPOSITION","RESULT",
        "SONUC_SIRA","SONUCNUMARASI"
    ]))

def unwrap_data(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload

def parse_full(payload, day, track):
    """
    Parse e-Bayi result payloads with inherited race context.
    Supports normal row dictionaries and several compact/parallel-array forms.
    """
    out, seen = [], set()
    payload = unwrap_data(payload)

    def parse_kosu_entry(kosu, fallback_race=None):
        if not isinstance(kosu, dict):
            return []
        race_no = extract_race_number(kosu) or fallback_race
        distance = parse_distance(pick(kosu, ["MESAFE","DISTANCE","UZUNLUK","KOSUMESAFESI"]))
        entries = []
        # TJK e-Bayi result payloads expose races under "kosular".
        # Each race may contain horse rows under several historical key names.
        for key, value in kosu.items():
            lk = norm(key)
            if lk in {"atlar","atlarlistesi","horses","horse","sonuclar","sonuc","sonuclarlistesi","koşular","kosular"} and isinstance(value, list):
                entries.extend((x, race_no, distance) for x in value if isinstance(x, dict))
        if not entries:
            entries.append((kosu, race_no, distance))
        return entries

    def add_row(node, race_no, distance, horse, finish):
        if not (is_horse_name(horse) and race_no and finish and 1 <= finish <= 30):
            return
        horse = str(horse).strip()
        key = (day, track, race_no, horse.casefold())
        if key in seen:
            return
        seen.add(key)
        out.append({
            "race_id": f"{day}-{track}-{race_no}",
            "race_date": day,
            "race_number": race_no,
            "track": track,
            "distance": distance,
            "horse": horse,
            "finish_position": finish,
            "jockey": str(pick(node, ["JOKEY","JOKEYADI","JOCKEY","JOKEYAD","JOKEADI"]) or "").strip(),
            "trainer": str(pick(node, ["ANTRENOR","ANTRENORADI","TRAINER","ANTRENOR_ADI"]) or "").strip(),
            "weight": num(pick(node, ["KILO","WEIGHT"])),
            "hp": num(pick(node, ["HC","HP","HANDIKAPPUANI","HANDIKAP","HCP"])),
            "agf_score": min(num(pick(node, ["AGF","AGFORAN","AGF_ORAN"])), 100),
            "odds": num(pick(node, ["GNY","GANYAN","ODDS","ORAN"])),
        })

    def visit(node, inherited_race=None, inherited_distance=0):
        if isinstance(node, dict):
            own_race = extract_race_number(node) or inherited_race
            own_distance = parse_distance(pick(node, ["MESAFE","DISTANCE","UZUNLUK","KOSUMESAFESI"])) or inherited_distance
            horse = pick(node, [
                "ATADI", "AT_ADI", "AT", "HORSE", "HORSE_NAME",
                "ATADI1", "ATADI2", "ATADI3", "ATADI4", "ATADI5",
                "ATADI6", "ATADI7", "ATADI8", "ATADI9", "ATADI10",
                "ATADI11", "ATADI12", "ATADI13", "ATADI14", "ATADI15",
                "ADI", "ATADIADI"
            ])
            fin = extract_finish(node)
            add_row(node, own_race, own_distance, horse, fin)
            for key, value in node.items():
                visit(value, own_race, own_distance)
        elif isinstance(node, list):
            for v in node:
                visit(v, inherited_race, inherited_distance)
        if isinstance(node, dict):
            own_race = extract_race_number(node) or inherited_race
            own_distance = parse_distance(pick(node, ["MESAFE","DISTANCE","UZUNLUK"])) or inherited_distance

            horse = pick(node, [
                "ATADI", "AT_ADI", "AT", "HORSE", "HORSE_NAME",
                "ATADI1", "ATADI2", "ATADI3", "ATADI4", "ATADI5",
                "ATADI6", "ATADI7", "ATADI8", "ATADI9", "ATADI10",
                "ATADI11", "ATADI12", "ATADI13", "ATADI14", "ATADI15",
                "ADI", "ATADIADI"
            ])
            fin = extract_finish(node)
            add_row(node, own_race, own_distance, horse, fin)

            horse_vals = pick(node, ["HORSES", "ATADI", "AT_ADI"])
            finish_vals = pick(node, ["RESULTS", "SONUCLAR", "SONUCNO", "SONUC", "SIRANO"])
            if own_race and isinstance(horse_vals, list) and isinstance(finish_vals, list):
                for h, f in zip(horse_vals, finish_vals):
                    add_row(node, own_race, own_distance, h, integer(f))

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
            if not got:
                try:
                    raw = unwrap_data(payload)
                    shape = type(raw).__name__
                    keys = list(raw.keys())[:30] if isinstance(raw, dict) else []
                    extra = ""
                    if isinstance(raw, dict) and isinstance(raw.get("kosular"), list):
                        ks = raw["kosular"]
                        if ks:
                            first = ks[0]
                            if isinstance(first, dict):
                                nested = {str(k): type(v).__name__ for k, v in list(first.items())[:25]}
                                extra = f" kosular_len={len(ks)} first_keys={list(first.keys())[:25]} first_types={nested}"
                            else:
                                extra = f" kosular_len={len(ks)} first_type={type(first).__name__}"
                        else:
                            extra = " kosular_len=0"
                    print(f"[local-backfill] {day} {name}: parser=0 payload={shape} keys={keys}{extra}")
                except Exception:
                    pass
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
    # Do not retain the legacy malformed rows (distance=0 / time-like horse names).
    if OUT.exists():
        with OUT.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    good = (
                        is_turkish_track(r.get("track",""))
                        and not is_time_like(r.get("horse",""))
                        and num(r.get("distance")) >= 1000
                        and int(float(r.get("finish_position","0"))) >= 1
                    )
                except (TypeError, ValueError):
                    good = False
                if good:
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
