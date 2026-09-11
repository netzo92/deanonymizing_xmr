---
summary: Deterministic labels, holdout measurement, and forward verification boundaries.
status: maintained
reviewed: 2026-09-11
---

# Evaluation

Parent: [research](index.md).

Sources: [RingScorer](../../scorer.py), [Database](../../models.py),
[CLI workflows](../../main.py),
[verification tests](../../tests/test_prediction_verification.py).

## Current training behavior

`build_training_data()` uses deterministic resolutions. `train()` splits by ring
with seed 42 and default holdout fraction 0.2; candidates from a ring stay
together. Scaling is fit on the training split. The implementation uses a
500-tree random forest with minimum leaf size 3 and balanced class weights,
then refits the scaler and model on all available training rings for prediction.

Holdout accuracy counts rings where the highest-scored member matches the label.
It is not candidate-level accuracy. A ring split prevents sharing candidates of
the same ring across splits, but does not establish independence between related
rings. Some graph-derived features use the current scanned snapshot, so the
measurement is not a full forward-time evaluation.

## Forward verification

Use a database kept free of exploratory soft-cascade claims:

1. Run `predict` to perform analysis, train, and save predictions without applying ML guesses to `resolved_spends`.
2. Later scan additional blocks into that same database.
3. Run `verify`, which runs analysis before comparing saved guesses with deterministic resolutions.
4. Report correct, wrong, verified, and unverified counts together.

Commands and explicit database paths are in
[development](../workflows/development.md). `analyze --score` applies ML guesses
and their cascades; subsequent plain analysis retains stored hypotheses. Use a
separate experimental database for that workflow.

Verification reads deterministic labels from the database rather than trusting
all in-memory analyzer resolutions. Hypotheses cannot serve as verification
labels. Agreement with deterministic evidence remains conditional on the
underlying data and analysis assumptions.

## Open measurement questions

- Does holdout performance transfer to later scan heights and unresolved rings?
- How much do shared outputs and snapshot-derived features affect validation?
- How representative is the verified subset of all saved predictions?
- Are model scores calibrated as probabilities? A confidence threshold alone does not demonstrate calibration.

These are questions to investigate, not established findings or assigned tasks.
New scoring runs retain model/scaler artifacts, source/feature metadata, candidate
scores and raw feature values. Older imported predictions have unknown model and
feature context. Resolution lineage itself still records the selected output and
confidence rather than embedding model artifacts. Complete forward evaluation
also needs frozen label eligibility and independent temporal splits; see
[prediction research](predictions.md).

Related: [storage and evidence](../architecture/storage-and-evidence.md),
[historical experiment records](index.md).
