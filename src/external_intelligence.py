from __future__ import annotations

"""Multi-source external racing intelligence layer."""

import json
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"

SOURCE_WEIGHTS = {
    "timeform": 1.00, "racingtv": 0.95, "racing post": 0.95,
    "at the races": 0.90, "sporting life": 0.90, "official": 0.90,
    "local": 0.80, "other": 0.50,
}

def _num(value, default=0.0):
    try: return float(value)
    except (TypeError, ValueError): return default

def _key(value):
    return " ".join(str(value or "").upper().split())

def _source_weight(name):
    n = str(name or "").strip().lower()
    for key, weight in SOURCE_WEIGHTS.items():
        if key in n: return weight
    return SOURCE_WEIGHTS["other"]

def load_signals():
    path = DATA / "external_signals.json"
    if not path.exists(): return []
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return []
    if isinstance(payload, dict): payload = payload.get("signals", [])
    return [x for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []

def merge_external_signals(rows, signals=None):
    signals = load_signals() if signals is None else signals
    grouped = defaultdict(list)
    for signal in signals:
        race = str(signal.get("race") or signal.get("race_number") or "").strip()
        horse = _key(signal.get("horse"))
        if race and horse: grouped[(race, horse)].append(signal)

    out = []
    for row in rows:
        item = dict(row)
        key = (str(row.get("race") or row.get("race_number") or "").strip(), _key(row.get("horse")))
        matches = grouped.get(key, [])
        defaults = {"external_tip_score":0.0,"external_tip_count":0.0,"source_consensus":0.0,
                    "trainer_uplift":0.0,"jockey_uplift":0.0,"horse_in_focus":0.0,"warning_flag":0.0,
                    "pace_score":50.0,"sectional_score":50.0,"course_fit":50.0,"distance_fit":50.0,
                    "timeform_rating":0.0,"timefigure":0.0}
        if not matches:
            for k,v in defaults.items(): item.setdefault(k,v)
            out.append(item); continue
        weights = [_source_weight(s.get("source")) for s in matches]
        wsum = sum(weights) or 1.0
        def weighted(name, default=0.0):
            return sum(_num(s.get(name), default)*w for s,w in zip(matches,weights))/wsum
        positives = sum(w for s,w in zip(matches,weights) if any(_num(s.get(k)) > 0 for k in ("tip","smart_stat","comment_positive")))
        negative = sum(w for s,w in zip(matches,weights) if _num(s.get("warning")) > 0)
        item["external_tip_score"] = round(max(-100.0,min(100.0, weighted("tip")*60 + weighted("smart_stat")*20 + weighted("comment_positive")*20 - weighted("warning")*40)),3)
        item["external_tip_count"] = len(matches)
        item["source_consensus"] = round(max(0.0,min(100.0,100*positives/wsum)),3)
        item["trainer_uplift"] = round(max(0.0,min(100.0,weighted("trainer_uplift")*100)),3)
        item["jockey_uplift"] = round(max(0.0,min(100.0,weighted("jockey_uplift")*100)),3)
        item["horse_in_focus"] = round(max(0.0,min(100.0,weighted("horse_in_focus")*100)),3)
        item["warning_flag"] = round(max(0.0,min(100.0,100*negative/wsum)),3)
        for name,default in (("pace_score",50),("sectional_score",50),("course_fit",50),("distance_fit",50),("timeform_rating",0),("timefigure",0)):
            item[name] = round(max(0.0,weighted(name,default)),3)
        out.append(item)
    return out

def build_source_report(signals=None):
    signals = load_signals() if signals is None else signals
    by_source = defaultdict(int)
    for s in signals: by_source[str(s.get("source") or "unknown")] += 1
    return {"signal_count":len(signals),"source_count":len(by_source),"sources":dict(sorted(by_source.items())),
            "source_weights":SOURCE_WEIGHTS,"purpose":"External signals are corroboration features, not guaranteed outcome predictions."}
