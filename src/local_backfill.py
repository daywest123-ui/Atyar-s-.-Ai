from __future__ import annotations
import csv, os, re
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data"; OUT=DATA/"historical_results.csv"
BASE="https://ebayi.tjk.org/s/d"
FIELDS=["race_id","race_date","race_number","track","distance","horse","finish_position","jockey","trainer","weight","hp","agf_score","odds"]
H={"User-Agent":"Mozilla/5.0 AtYarisiAI/LocalCollector","Accept":"application/json,text/plain,*/*"}

def norm(v): return re.sub(r"[^a-z0-9çğıöşü]","",str(v).lower())
def pick(o,names):
    if not isinstance(o,dict): return None
    ns={norm(x) for x in names}
    for k,v in o.items():
        if norm(k) in ns: return v
    for k,v in o.items():
        if any(x in norm(k) for x in ns): return v
    return None
def num(v):
    try:return float(str(v).replace("%","").replace(",",".").strip())
    except:return 0.0
def integer(v):
    m=re.search(r"(?<!\d)(\d{1,3})(?!\d)",str(v or ""))
    return int(m.group(1)) if m else None
def walk(n):
    if isinstance(n,dict):
        yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n,list):
        for v in n: yield from walk(v)

def parse(payload,day,track):
    out=[]; seen=set()
    for o in walk(payload):
        horse=pick(o,["ATADI","AT_ADI","AT","HORSE","HORSE_NAME","ATADI3"])
        fin=pick(o,["SONUCNO","SONUC","SIRA","SIRANO","FINISH","FINISHPOSITION","RESULT"])
        race=pick(o,["KOSUNO","RACENO","RACE_NO","RACE","KOSU"])
        if not horse or race is None or fin is None: continue
        rn,fp=integer(race),integer(fin)
        if rn is None or fp is None: continue
        horse=str(horse).strip(); key=(day,track,rn,horse)
        if key in seen: continue
        seen.add(key)
        out.append({"race_id":f"{day}-{track}-{rn}","race_date":day,"race_number":rn,"track":track,
          "distance":integer(pick(o,["MESAFE","DISTANCE","UZUNLUK"])) or 0,"horse":horse,"finish_position":fp,
          "jockey":str(pick(o,["JOKEY","JOKEYADI","JOCKEY","JOKEYAD"]) or ""),
          "trainer":str(pick(o,["ANTRENOR","ANTRENORADI","TRAINER"]) or ""),
          "weight":num(pick(o,["KILO","WEIGHT"])),"hp":num(pick(o,["HC","HP","HANDIKAPPUANI","HANDIKAP"])),
          "agf_score":min(num(pick(o,["AGF","AGFORAN","AGF_ORAN"])),100),"odds":num(pick(o,["GNY","GANYAN","ODDS","ORAN"]))})
    return out

def get(url):
    r=requests.get(url,headers=H,timeout=(10,30)); r.raise_for_status(); return r.json()

def day_rows(day):
    ds=day.replace("-",""); idx=get(f"{BASE}/sonuclar/{ds}/yarislar.json")
    items=idx.get("data",idx) if isinstance(idx,dict) else idx
    tracks=[]
    for x in items if isinstance(items,list) else []:
        if isinstance(x,dict):
            k=pick(x,["KEY","KOD","CODE"]); name=pick(x,["AD","YER","NAME"]) or k
            if k: tracks.append((str(k),str(name)))
    rows=[]
    for k,name in tracks:
        try:
            got=parse(get(f"{BASE}/sonuclar/{ds}/full/{k}.json"),day,name); rows+=got
            print(f"[local-backfill] {day} {name}: {len(got)} rows")
        except Exception as e: print(f"[local-backfill] {day} {name}: {e}")
    return rows

def main():
    from datetime import date,timedelta
    days=max(1,min(int(os.getenv("BACKFILL_DAYS","30")),90))
    end=date.today()-timedelta(days=1); dates=[end-timedelta(days=i) for i in range(days-1,-1,-1)]
    DATA.mkdir(exist_ok=True); existing={}
    if OUT.exists():
        with OUT.open(encoding="utf-8",newline="") as f:
            for r in csv.DictReader(f): existing[(r.get("race_id"),r.get("horse"))]=r
    for d in dates:
        ds=d.isoformat()
        try: rows=day_rows(ds)
        except Exception as e: print(f"[local-backfill] DAY FAILED {ds}: {e}"); continue
        for r in rows: existing[(r["race_id"],r["horse"])]=r
        with OUT.open("w",encoding="utf-8",newline="") as f:
            w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(existing.values())
        print(f"[local-backfill] checkpoint {ds}: {len(existing)} rows")
    print(f"[local-backfill] DONE: {len(existing)} rows")
    if not existing: raise SystemExit("Yerel TJK e-Bayi bağlantısı veri döndürmedi.")
if __name__=="__main__": main()
