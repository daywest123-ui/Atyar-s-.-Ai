from __future__ import annotations

import json
from pathlib import Path

from scoring import rank_horses


BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)


def main() -> None:
    source = DATA / "horses.json"
    if not source.exists():
        print("No horse data yet. TJK collector will populate data/horses.json.")
        return

    rows = json.loads(source.read_text(encoding="utf-8"))
    ranked = rank_horses(rows)
    out = DATA / "ranked_horses.json"
    out.write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")
    for i, horse in enumerate(ranked, 1):
        print(f"{i:02d}. {horse.get('horse', 'Unknown')} - {horse['score']}")


if __name__ == "__main__":
    main()
