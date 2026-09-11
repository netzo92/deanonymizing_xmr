---
summary: Evidence standards and pointers to historical experiments.
status: maintained
reviewed: 2026-09-11
---

# Research

Parent: [repository brain](../index.md).

Research results depend on label provenance, the scanned snapshot, and the
evaluation procedure. Stored resolutions describe output-selection claims;
they do not by themselves establish a person's identity.

Read [evaluation](evaluation.md) for training, holdout, forward verification,
and unresolved measurement questions.

Proposed future work:

- [Prediction experiments](predictions.md): historical runs, time-based evaluation, calibration, and abstention.
- [Grouping research](groupings.md): output/transaction relationships and cluster validation.
- [Ideas discovered during implementation](ideas.md): prediction changes, label drift, coverage gaps, and cluster stability.
- [Prioritized TODOs](../tasks/index.md): implementation order and dashboard acceptance criteria.

These proposals are a user-requested backlog, not implemented findings.

Existing experiment records:

- [Autoresearch notes](../../autoresearch_results.md): historical baseline and feature importances.
- [Iteration table](../../autoresearch-results.tsv): logged models and metrics.
- [README](../../README.md): example holdout and forward-verification snapshots.

These records describe different runs and procedures. Do not combine their
numbers into a current benchmark or assume they describe the current model.
New results should record the code revision, local changes, dataset/scan height,
command, evaluation type, sample counts, and limitations. Follow
[maintenance](../workflows/maintenance.md) when adding a result page.
