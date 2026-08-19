from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from history import enrich_horses

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AtYarisiAI/1.0)"}


def _number(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip().replace("%", "").replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return 0.0


def _form_score(form: str | None) -> float:
    if not form:
        return 0.0
    values = []
    for char in form:
        if char.isdigit():
            pos = int(char)
            values.append(10.0 if pos == 0 else max(0.0, 100.0 - (pos - 1) * 15.0))
    return round(sum(values) / len(values), 2) if values else 0.0


def collect_nalsesleri() -> list[dict]:
    url = os.getenv("NALSESLERI_PROGRAM_URL", "https://nalsesleri.com/program")
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    rows: list[dict] = []

    for table in soup.find_all("table"):
        headers = [th.get_text(" ", strip=True).upper() for th in table.find_all("th")]
        if not headers or "AT" not in headers:
            continue
        for tr in table.find_all("tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) != len(headers):
                continue
            raw = dict(zip(headers, cells))
            horse = raw.get("AT", "").strip()
            if not horse:
                continue
            rows.append({
                "horse": horse,
                "race": raw.get("YARIŞ", raw.get("KOŞU", raw.get("KOSU", ""))),
                "start": raw.get("START", raw.get("NO", "")),
                "jockey": raw.get("JOKEY", ""),
                "trainer": raw.get("ANTRENÖR", raw.get("ANTRENOR", "")),
                "form": raw.get("SON 6", ""),
                "weight": _number(raw.get("KG")),
                "agf_score": min(_number(raw.get("AGF %")), 100.0),
                "recent_form": _form_score(raw.get("SON 6", "")),
                "track_form": 50.0,
                "distance_form": 50.0,
                "jockey_form": 50.0,
                "trainer_form": 50.0,
                "weight_score": 50.0,
            })
    return rows


def collect() -> list[dict]:
    rows = collect_nalsesleri()
    if not rows:
        return rows
    return enrich_horses(rows)


def main() -> None:
    rows = collect()
    payload = {"date": date.today().isoformat(), "count": len(rows), "horses": rows}
    (DATA / "horses.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "raw_program.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected and enriched {len(rows)} horses")


if __name__ == "__main__":
    main()
