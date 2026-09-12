---
summary: Prioritized TODOs for dashboard improvements and prediction research.
status: maintained
reviewed: 2026-09-11
---

# TODOs

Parent: [repository brain](../index.md).

User-requested backlog. All unchecked items are proposed, not implemented.
Priority is a suggested order, not an estimate of research success.

| Priority | Work | Completion condition |
| --- | --- | --- |
| Done | D1: Separate evidence classes | Counts reconcile with persisted claims |
| Done | P1: Preserve prediction runs | Re-scoring retains earlier guesses and outcomes |
| Done | D2–D3: Prediction browser and evidence inspector | Bounded exports with run/provenance metadata |
| Done | D5/D7: Local evidence graph and visual knowledge brain | Typed edges, searchable notes and TODOs, keyboard/mobile access |
| Done | D9–D11: Timeline, direct groupings, TODO/results and current-chain feed | Dataset-scoped progress; source-linked conclusions; independently sampled recent blocks and periodic page checks |
| Done | EA1: Audit constant features | Frozen legacy matrix with 24 features, variation, duplicates, and cohort counts |
| P1 | FA2–FA3: Feature masks and reuse ties | Paired evaluation with training-only masks and explicit tie rules |
| P1 | P2–P3: Forward evaluation and calibration | Frozen data and independent evaluation boundaries |
| P1 | EA2/HE1: Measured output ages | Versioned origin joins and frozen ablation against current proxies |
| P2 | D4: Validated quality plots | Descriptive lab implemented; independent evaluation still required |
| P2 | G1–G2: Overlap and co-spend hypotheses | Relationship meanings tested on controlled data |
| P3 | G3–G4: Experimental clusters | Labeled evaluation and false-merge analysis |

Canonical checklists: [dashboard](dashboard.md),
[prediction research](../research/predictions.md),
[grouping research](../research/groupings.md),
[heuristic experiments](../research/heuristic-experiments.md),
[measured output-origin finding](../research/output-origin-audit.md), and
[private-node observations](../research/private-node-observations.md).

First milestone completed: evidence-class counts, immutable prediction runs,
search/filter/pagination, and an evidence inspector. D6 usability improvements
are also complete. Validation: 45 Python tests, 6 JavaScript tests, real-model
artifact replay, and desktop/mobile Chrome interaction checks. The refreshed
dashboard uses a backup of the local dataset; the original database was not
migrated by this implementation session.

The new workspace milestone adds descriptive analytics, a typed local evidence
graph, and a navigable Markdown brain with TODO filters; see the
[workspace guide](../workflows/research-workspace.md). A 40-output audit found
three constant legacy gamma features despite widely varying measured ages.
EA1 is now complete for the available historical snapshot; see the
[feature audit](../research/feature-variation-audit.md) and
[retrospective baseline results](../research/heuristic-baselines.md).
Next: FA2 feature-mask ablation, FA3 reuse tie treatment, HE1 measured origins,
and EA2 origin-age ablation, alongside P2 forward evaluation and P3 calibration.
Clustering remains experimental work requiring validation; no model-accuracy
improvement has been established.

When taking an item, record status and implementation/result links in its
canonical page. Check it off only after its completion criteria are met. Keep
research results tied to the dataset, revision, method, and limitations.
