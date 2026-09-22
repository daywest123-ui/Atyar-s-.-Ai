from __future__ import annotations

import json
from pathlib import Path

from advanced_model import enrich
from external_intelligence import merge_external_signals
from scoring import rank_horses

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)


def main() -> None:
    source = DATA / "horses.json"
    if not source.exists():
        print("No horse data yet. Run collector first.")
        return

    rows = json.loads(source.read_text(encoding="utf-8"))

    rows = merge_external_signals(rows)

    # Keep the original transparent score for continuity.
    baseline = rank_horses(rows)
    (DATA / "ranked_horses.json").write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Add the integrated components inspired by the compatible open-source
    # TJK architecture: ranking, Bayesian shrinkage, fair odds, edge and
    # Harville-ready race probabilities.
    advanced = enrich(rows)
    (DATA / "advanced_ranked_horses.json").write_text(
        json.dumps(advanced, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for i, horse in enumerate(advanced, 1):
        print(
            f"{i:02d}. {horse.get('horse', 'Unknown')} "
            f"p={horse.get('model_probability', 0):.2%} "
            f"fair={horse.get('fair_odds')} "
            f"edge={horse.get('edge')}"
        )


if __name__ == "__main__":
    main()
