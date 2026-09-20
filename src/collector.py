from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from io import StringIO
from datetime import date, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from history import enrich_horses

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AtYarisiAI/2.0)",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}

TJK_BASE = "https://www.tjk.org"
TJK_PROGRAM_PAGE = "/TR/YarisSever/Info/Page/GunlukYarisProgrami"
TJK_PROGRAM_CITY = "/TR/YarisSever/Info/Sehir/GunlukYarisProgrami"
DOMESTIC_SEHIR_IDS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}


def _number(value: str | None) -> float:
    if not value:
        return 0.0
    value = value.strip().replace("%", "")
    # TJK percentages/decimals may use Turkish commas.
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    else:
        # Keep ordinary decimals such as 12.5 intact.
        value = value.replace(" ", "")
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


def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _link_text(node) -> str:
    if not node:
        return ""
    link = node.select_one("a")
    return link.get_text(" ", strip=True) if link else _text(node)


def _safe_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group()) if m else None


def _parse_tjk_program(html: str, race_date: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []

    # Current TJK program markup uses race panes + tablesorter tables.
    panes = soup.select("div.races-panes > div")
    if not panes:
        panes = soup.select("div.race-details")

    for pane in panes:
        detail = pane.select_one("div.race-details") if pane.name != "div" or "race-details" not in (pane.get("class") or []) else pane
        if detail is None:
            detail = pane

        race_text = _text(detail.select_one("h3.race-no"))
        race_match = re.search(r"(\d+)\.\s*Koşu", race_text, re.I)
        race_no = int(race_match.group(1)) if race_match else _safe_int(race_text)
        if race_no is None:
            continue

        config = _text(detail.select_one("h3.race-config"))
        distance = _safe_int(config)

        table = pane.select_one("table.tablesorter") if hasattr(pane, "select_one") else None
        if table is None:
            continue

        for tr in table.select("tbody tr"):
            name_cell = tr.select_one("td.gunluk-GunlukYarisProgrami-AtAdi")
            if not name_cell:
                continue
            name_link = name_cell.select_one("a")
            horse = name_link.get_text(" ", strip=True) if name_link else _text(name_cell)
            horse = re.sub(r"\(\d+\)", "", horse).strip()
            if not horse:
                continue

            # Scratched horses are kept in the source but excluded from modelling.
            scratched = "Koşmaz" in str(name_cell) or "Kosmaz" in str(name_cell)

            age = _text(tr.select_one("td.gunluk-GunlukYarisProgrami-Yas"))
            weight = _number(_text(tr.select_one("td.gunluk-GunlukYarisProgrami-Kilo")))
            agf_text = _text(tr.select_one("td.gunluk-GunlukYarisProgrami-AGFORAN"))
            agf = _number(agf_text)

            rows.append({
                "horse": horse,
                "race": f"{race_no}",
                "race_number": race_no,
                "date": race_date.isoformat(),
                "start": _text(tr.select_one("td.gunluk-GunlukYarisProgrami-SiraId")),
                "jockey": _link_text(tr.select_one("td.gunluk-GunlukYarisProgrami-JokeAdi")),
                "trainer": _link_text(tr.select_one("td.gunluk-GunlukYarisProgrami-AntronorAdi")),
                "form": _text(tr.select_one("td.gunluk-GunlukYarisProgrami-Son6Yaris")),
                "weight": weight,
                "agf_score": min(agf, 100.0),
                "recent_form": _form_score(_text(tr.select_one("td.gunluk-GunlukYarisProgrami-Son6Yaris"))),
                "track_form": 50.0,
                "distance_form": 50.0,
                "jockey_form": 50.0,
                "trainer_form": 50.0,
                "weight_score": 50.0,
                "distance": distance,
                "scratched": scratched,
            })
    return rows


def collect_tjk() -> list[dict]:
    """Collect today's domestic TJK program through the public TJK AJAX pages."""
    target_date = date.today()
    date_str = target_date.strftime("%d.%m.%Y")

    session = requests.Session()
    session.headers.update(HEADERS)

    main_url = f"{TJK_BASE}{TJK_PROGRAM_PAGE}"
    response = session.get(
        main_url,
        params={"QueryParameter_Tarih": date_str},
        timeout=30,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    tabs = soup.select("ul.gunluk-tabs li a[data-sehir-id]")
    if not tabs:
        raise RuntimeError(
            "TJK program sayfası açıldı ancak şehir sekmeleri bulunamadı; "
            "TJK HTML yapısı değişmiş olabilir."
        )

    tracks = []
    for tab in tabs:
        try:
            sid = int(tab.get("data-sehir-id", ""))
        except (TypeError, ValueError):
            continue
        if sid not in DOMESTIC_SEHIR_IDS:
            continue
        name = re.sub(r"\s*\(\d+\.\s*Y\.G\.\)\s*$", "", _text(tab)).strip()
        tracks.append((sid, name))

    all_rows: list[dict] = []
    for sid, track_name in tracks:
        r = session.get(
            f"{TJK_BASE}{TJK_PROGRAM_CITY}",
            params={
                "SehirId": str(sid),
                "QueryParameter_Tarih": date_str,
                "SehirAdi": track_name,
            },
            timeout=30,
        )
        r.raise_for_status()
        city_rows = _parse_tjk_program(r.text, target_date)
        for row in city_rows:
            row["track"] = track_name
        all_rows.extend(city_rows)

    if not all_rows:
        raise RuntimeError(
            "TJK bağlantısı başarılı fakat bugünün programından 0 at ayrıştırıldı."
        )

    # Never feed scratched horses into the prediction model.
    return [r for r in all_rows if not r.get("scratched")]



def collect_tjk_pdf() -> list[dict]:
    """Fallback: parse TJK daily PDF from the public CDN when AJAX times out."""
    target_date = date.today()
    day = target_date.isoformat()
    url = (
        f"https://medya-cdn.tjk.org/raporftp/TJKPDF/{target_date:%Y}/"
        f"{day}/PDFOzet/GunlukYarisProgrami/"
        f"{target_date:%d.%m.%Y}-Karma-GunlukYarisProgrami-TR.pdf"
    )
    response = requests.get(url, headers=HEADERS, timeout=(15, 60))
    response.raise_for_status()

    from pypdf import PdfReader
    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = Path(tmp) / "program.pdf"
        pdf_path.write_bytes(response.content)
        reader = PdfReader(str(pdf_path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)

    rows: list[dict] = []
    race_no = None
    distance = None
    track = ""
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue

        title = re.search(r"^(\d{2}\.\d{2}\.\d{4})\s+(.+?)\s*-\s*Yarış Programı", line)
        if title:
            track = title.group(2).strip()
            continue

        race = re.match(r"^(\d+)\.\s*Koşu$", line)
        if race:
            race_no = int(race.group(1))
            distance = None
            continue

        if race_no is None:
            continue

        dist = re.search(r"(\d{3,4})m\.", line)
        if dist:
            distance = int(dist.group(1))

        m = re.match(
            r"^(\d+)\((\d+)\)\s+(.+?)\s+(\d+[yY])\s+"
            r"([\d]+(?:[.,]\d+)?)\s+"
            r"([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ.\-]+)\s+"
            r"(.+?)\s+"
            r"(\d+(?:[.,]\d+)?)\s+"
            r"(\d+(?:[.,]\d+)?)\s+"
            r"(\d+(?:[.,]\d+)?)"
            r"(?:\s+([0-9\-]+))?$",
            line,
        )
        if not m:
            continue

        no, gate, horse_raw, age, weight, jockey, owner, hp, kgs, s20, form = m.groups()
        horse = re.sub(r"\s+(?:KG|DB|SKG|SK|K|GKR)(?=\s|$)", " ", horse_raw).strip()
        if not horse:
            continue

        rows.append({
            "horse": horse,
            "race": str(race_no),
            "race_number": race_no,
            "date": target_date.isoformat(),
            "start": no,
            "gate": gate,
            "track": track,
            "jockey": jockey.strip(),
            "trainer": "",
            "owner": owner.strip(),
            "form": form or "",
            "weight": _number(weight),
            "agf_score": 0.0,
            "recent_form": _form_score(form or ""),
            "track_form": 50.0,
            "distance_form": 50.0,
            "jockey_form": 50.0,
            "trainer_form": 50.0,
            "weight_score": 50.0,
            "distance": distance or 0,
            "hp": _number(hp),
            "kgs": _number(kgs),
            "s20": _number(s20),
            "scratched": False,
        })

    if not rows:
        raise RuntimeError("TJK günlük PDF indirildi fakat at satırları ayrıştırılamadı.")
    return rows


def collect_public_html_fallback() -> list[dict]:
    """Robust HTML fallback using the site's rendered table structure, not pandas column inference."""
    target_date = date.today()
    month_names = {
        1: "ocak", 2: "subat", 3: "mart", 4: "nisan", 5: "mayis", 6: "haziran",
        7: "temmuz", 8: "agustos", 9: "eylul", 10: "ekim", 11: "kasim", 12: "aralik",
    }
    weekdays = ["pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar"]
    slug = f"{target_date.day}-{month_names[target_date.month]}-{target_date.year}-{weekdays[target_date.weekday()]}"
    urls = [
        f"https://www.agftablosu.com/at-yarisi/karma/{slug}",
        f"https://www.agftablosu.com/at-yarisi/karma/{target_date:%d-%m-%Y}",
        f"https://www.agftablosu.com/at-yarisi/karma/{target_date:%d-%B-%Y}-{weekdays[target_date.weekday()]}",
    ]

    def norm(value: str) -> str:
        value = value.strip().lower()
        value = value.replace("ı", "i").replace("ş", "s").replace("ğ", "g")
        value = value.replace("ü", "u").replace("ö", "o").replace("ç", "c")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    last_error = None
    for page_url in urls:
        try:
            response = requests.get(page_url, headers=HEADERS, timeout=(15, 30))
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            rows: list[dict] = []
            race_numbers: set[int] = set()

            for table in soup.find_all("table"):
                trs = table.find_all("tr")
                if len(trs) < 2:
                    continue

                header_cells = trs[0].find_all(["th", "td"])
                headers = [norm(cell.get_text(" ", strip=True)) for cell in header_cells]
                header_map = {h: i for i, h in enumerate(headers) if h}

                def find_col(*names):
                    wanted = {norm(x) for x in names}
                    for h, i in header_map.items():
                        if h in wanted:
                            return i
                    for h, i in header_map.items():
                        if any(x in h for x in wanted):
                            return i
                    return None

                i_horse = find_col("at ismi", "at")
                i_weight = find_col("kilo", "kg")
                i_jockey = find_col("jokey")
                i_start = find_col("st", "start")
                i_form = find_col("son 6 y", "son 6")
                i_hp = find_col("hk", "h k", "handikap")

                if i_horse is None or i_weight is None or i_jockey is None or i_start is None or i_form is None:
                    continue

                heading = table.find_previous(["h1", "h2", "h3", "h4", "h5", "h6"])
                heading_text = heading.get_text(" ", strip=True) if heading else ""
                race_match = re.search(r"(\d+)\s*\.\s*Koşu", heading_text, re.I)
                if not race_match:
                    # Also search nearby parent text for headings such as "Karma 1. Koşu".
                    parent_text = table.parent.get_text(" ", strip=True) if table.parent else ""
                    race_match = re.search(r"(\d+)\s*\.\s*Koşu", parent_text, re.I)
                if not race_match:
                    continue
                race_no = int(race_match.group(1))
                race_numbers.add(race_no)

                for tr in trs[1:]:
                    cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
                    if len(cells) <= max(i_horse, i_weight, i_jockey, i_start, i_form):
                        continue

                    horse = cells[i_horse].strip()
                    if not horse or norm(horse) in {"at ismi", "at"}:
                        continue
                    if horse.lower() == "nan":
                        continue

                    form = cells[i_form].strip()
                    start = _safe_int(cells[i_start])
                    weight = _number(cells[i_weight])
                    jockey = cells[i_jockey].strip()
                    hp = _number(cells[i_hp]) if i_hp is not None and i_hp < len(cells) else 0.0

                    rows.append({
                        "horse": horse,
                        "race": str(race_no),
                        "race_number": race_no,
                        "date": target_date.isoformat(),
                        "start": start or "",
                        "gate": start or "",
                        "track": "Karma",
                        "jockey": jockey,
                        "trainer": "",
                        "owner": "",
                        "form": form,
                        "weight": weight,
                        "agf_score": 0.0,
                        "recent_form": _form_score(form),
                        "track_form": 50.0,
                        "distance_form": 50.0,
                        "jockey_form": 50.0,
                        "trainer_form": 50.0,
                        "weight_score": 50.0,
                        "distance": 0,
                        "hp": hp,
                        "kgs": 0.0,
                        "s20": 0.0,
                        "scratched": False,
                    })

            if rows and len(race_numbers) >= 6:
                print(f"Public HTML parsed: {len(rows)} horses / {len(race_numbers)} races")
                return rows
            last_error = RuntimeError(
                f"HTML sayfası açıldı ancak yeterli yarış tablosu bulunamadı "
                f"(atlar={len(rows)}, yarışlar={sorted(race_numbers)})"
            )
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"Public HTML fallback başarısız: {last_error}")

def collect_nalsesleri() -> list[dict]:
    """Legacy fallback source retained for resilience."""
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
    errors: list[str] = []

    # TJK is the primary source. The old source is only a fallback.
    try:
        rows = collect_tjk()
        source = "TJK"
    except Exception as exc:
        errors.append(f"TJK: {exc}")
        rows = []
        source = ""

    if not rows:
        try:
            rows = collect_tjk_pdf()
            source = "TJK PDF CDN fallback"
        except Exception as exc:
            errors.append(f"TJK PDF: {exc}")

    if not rows:
        try:
            rows = collect_public_html_fallback()
            source = "Public HTML fallback"
        except Exception as exc:
            errors.append(f"Public HTML: {exc}")

    if not rows:
        try:
            rows = collect_nalsesleri()
            source = "Nal Sesleri fallback"
        except Exception as exc:
            errors.append(f"Nal Sesleri: {exc}")

    if not rows:
        raise RuntimeError(
            "Hiçbir veri kaynağından yarış programı alınamadı.\n"
            + "\n".join(errors)
        )

    enriched = enrich_horses(rows)
    if not enriched:
        raise RuntimeError(f"{source} veri verdi fakat enrichment sonrası 0 at kaldı.")

    print(f"Data source: {source}")
    return enriched


def main() -> None:
    rows = collect()
    payload = {
        "date": date.today().isoformat(),
        "count": len(rows),
        "source": "TJK primary / Nal Sesleri fallback",
        "horses": rows,
    }
    (DATA / "horses.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DATA / "raw_program.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Collected and enriched {len(rows)} horses")


if __name__ == "__main__":
    main()
