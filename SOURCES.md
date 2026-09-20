# Open-source integration map

The project uses ideas and compatible interfaces rather than copying source
code from unrelated repositories.

## Reference projects

- `fatihbozdag/Ganyan` — TJK-oriented ranking/Bayesian/Harville architecture.
- `mervees/Horse-Racing-Analytics` — probability/fair-odds/edge/backtest concepts.
- `robinhowlett/rpscrape` — racing-data scraping reference; availability and
  website terms must be checked before using it for Turkish data.
- `dinoa7/Horse-Racing-Machine-Learning` — Monte Carlo simulation reference.

The local implementation is independently written and keeps the external
projects as architectural references. Their historical results are not
treated as evidence that this system will make a profit.

## Current pipeline

collector -> history -> feature/model layer -> race analysis -> 6'li optimizer

Outputs:
- data/horses.json
- data/advanced_ranked_horses.json
- data/race_analysis.json
- data/sixli_coupons.json

Before using real money, run the archived-data backtest and verify data
quality, leakage controls, and the legality/terms of each data source.
