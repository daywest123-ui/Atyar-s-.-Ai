from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AtYarisiAI/1.0; +https://github.com/daywest123-ui/Atyar-s-.-Ai)"
}


def _number(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip().replace("%", "").replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return 0.0


def collect_nalsesleri() -> list[dict]:
    """Free fallback collector. Nalsesleri publishes a tabular daily program."""
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
            if not horse or not raw.get("NO", "").strip().isdigit():
                continue
            rows.append(
                {
                    "horse": horse,
                    "race": raw.get("YARIŞ", raw.get("KOSU", "")),
                    "start": raw.get("START", raw.get("NO", "")),
                    "jockey": raw.get("JOKEY", ""),
                    "form": raw.get("SON 6", ""),
                    "weight": _number(raw.get("KG")),
                    "agf_score": min(_number(raw.get("AGF %")), 100.0),
                    "recent_form": _form_score(raw.get("SON 6", "")),
                    "track_form": 50.0,
                    "distance_form": 50.0,
                    "jockey_form": 50.0,
                    "trainer_form": 50.0,
                    "weight_score": 50.0,
                }
            )
    return rows


def _form_score(form: str | None) -> float:
    if not form:
        return 0.0
    values = []
    for char in form:
        if char.isdigit() and char != "0":
            pos = int(char)
            values.append(max(0.0, 100.0 - (pos - 1) * 15.0))
        elif char == "0":
            values.append(10.0)
    return round(sum(values) / len(values), 2) if values else 0.0


def collect() -> list[dict]:
    # The official TJK package documents a protected X-Auth API. We deliberately
    # do not hard-code or expose credentials. If supplied as GitHub Secrets, a
    # future adapter can use them. Until then the free public collector works.
    return collect_nalsesleri()


def main() -> None:
    rows = collect()
    payload = {"date": date.today().isoformat(), "count": len(rows), "horses": rows}
    (DATA / "horses.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "raw_program.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Collected {len(rows)} horses")


if __name__ == "__main__":
    main()
