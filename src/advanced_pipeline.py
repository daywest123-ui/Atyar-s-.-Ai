from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from advanced_model import enrich, race_summary
from sixli_optimizer import build_budgets

BASE = Path(__file__).resolve().parents[1]
DATA = BASE / "data"
DATA.mkdir(exist_ok=True)


def run() -> None:
    source = DATA / "horses.json"
    if not source.exists():
        print("No horse data yet. Run collector first.")
        return

    rows = json.loads(source.read_text(encoding="utf-8"))
    ranked = enrich(rows)
    (DATA / "advanced_ranked_horses.json").write_text(
        json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in ranked:
        key = str(row.get("race") or row.get("race_id") or "unknown")
        grouped[key].append(row)

    summaries = [race_summary(v) for v in grouped.values()]
    (DATA / "race_analysis.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sixli = build_budgets(ranked, budgets=(240, 720, 1440))
    (DATA / "sixli_coupons.json").write_text(
        json.dumps(sixli, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Advanced ensemble: {len(ranked)} horses / {len(summaries)} races")
    statuses = ", ".join(
        f"{budget}={payload.get('status', 'unknown')}"
        for budget, payload in sixli.items()
    )
    print("6'li optimizer: " + statuses)
    for r in summaries:
        top = (r.get("top3") or [{}])[0]
        print(
            f"Race {r['race']}: {top.get('horse')} "
            f"p={top.get('probability')} fair={top.get('fair_odds')} "
            f"skip_gate={r['skip_gate']}"
        )


if __name__ == "__main__":
    run()
