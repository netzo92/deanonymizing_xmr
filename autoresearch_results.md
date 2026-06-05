# Autoresearch Results — ML Holdout Accuracy

**Goal:** Improve ML holdout accuracy (% of test rings where top-scored member is the real spend)
**Metric:** ML holdout accuracy % (higher is better)
**Guard:** `python main.py status` (must succeed)
**Scope:** scorer.py

## Feature Importances at Baseline
- ring_reuse_count: 65.38% ← dominant
- gap_to_prev: 21.92%
- distance_from_tx: 9.13%
- normalized_age: 1.73%
- gap_to_next: 1.23%
- ring_size: 0.37%
- output_age_rank: 0.14%
- age_matches_decoy_distribution: 0.06%
- is_newest_member: 0.04%

## Iteration Log

| # | Description | Holdout Acc | Delta | Decision |
|---|-------------|-------------|-------|----------|
| 0 | Baseline (GBM, 9 features) | 86.1% | — | baseline |
