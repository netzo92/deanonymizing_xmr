---
summary: Proposed experiments for measurable predictions and useful uncertainty displays.
status: maintained
reviewed: 2026-09-11
---

# Prediction research

Parent: [research](index.md). Related: [TODO order](../tasks/index.md),
[evaluation](evaluation.md), [groupings](groupings.md).

Sources for current gaps: [scorer](../../scorer.py),
[prediction persistence](../../models.py), [export handler](../../main.py).
Unchecked items are proposals, not measured improvements. Completed infrastructure is linked below.

- [x] **P1 — Immutable prediction runs (P0).** Record revision/local-change fingerprint, dataset identity, scan cutoff, feature version, model/scaler reference, settings, and creation time. Persist per-run candidate scores and append verification events with verification height. Done when scoring the same ring twice retains both guesses and outcomes; migration marks unavailable historical metadata unknown. Implemented in [models.py](../../models.py) and [scorer.py](../../scorer.py), with [history tests](../../tests/test_prediction_history.py). The latest accepted cache remains compatible; its replacement no longer erases historical records. Failed scoring/verification operations roll back atomically. A real-model smoke check reproduced saved candidate scores from the artifact and frozen features.

- [ ] **P2 — Frozen forward evaluation (P1).** Freeze training/features at height H, save predictions, then evaluate against later evidence at H+Δ without rebuilding old features. Repeat across windows and report verified fraction alongside accuracy. Add shared-output/group splits as sensitivity checks for related rings. Done when later blocks cannot alter saved prediction-time features and results record sample counts, time windows, label provenance, and the unverified population.

- [ ] **P3 — Calibration and abstention (P1; depends on P1–P2).** Compare raw scores with held-out calibration and measure precision/coverage across acceptance thresholds. Separate chronological fitting, calibration, threshold-selection, and test data. Current binary candidate scores need not sum to one within a ring; normalization alone does not establish posterior probabilities. Done when frozen test reports include reliability bins, counts, calibration error, and coverage curves. Calibration on the verified subset must be labeled as such.

- [ ] **P4 — Baselines and ablations (P2; depends on P2).** Compare the random forest with random-member and simple age/reuse baselines on identical eligible rings. Remove feature families individually. Report ring top-1 accuracy at matched coverage, sliced by ring size and historical period. Account for related rings in uncertainty estimates. Done when claimed improvements persist across multiple frozen windows rather than only a random holdout.

- [ ] **P5 — Alternatives and ambiguity (P2; depends on P1–P3).** Retain candidate rankings, top-score margins, and cases below the current saving threshold. Investigate a ring-level ranking model reflecting one real candidate per ring. Measure top-k recall and top-1 precision at matched coverage. Display entropy as probability uncertainty only with a justified distribution. Done when the UI can show alternatives and explain abstention without treating an unvalidated score as certainty.

- [ ] **P6 — Error review and transfer (P2).** Inspect high-score mistakes, conflicts, legacy lineage, and performance by scan period/ring size. Compare estimated age features with accurate output-origin metadata, which requires ingestion changes. Done when reports distinguish data errors, label assumptions, and model errors and do not extrapolate historical performance to untested periods.

For each experiment record hypothesis, frozen run IDs, baseline, primary metric,
sample sizes, result, and decision. Keep negative or inconclusive findings linked.
