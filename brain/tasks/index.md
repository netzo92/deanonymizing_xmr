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
| P1 | P2–P3: Forward evaluation and calibration | Frozen data and independent evaluation boundaries |
| P2 | D4–D5: Quality plots and local graph | Score history and explicit edge types |
| P2 | G1–G2: Overlap and co-spend hypotheses | Relationship meanings tested on controlled data |
| P3 | G3–G4: Experimental clusters | Labeled evaluation and false-merge analysis |

Canonical checklists: [dashboard](dashboard.md),
[prediction research](../research/predictions.md),
[grouping research](../research/groupings.md).

First milestone completed: evidence-class counts, immutable prediction runs,
search/filter/pagination, and an evidence inspector. D6 usability improvements
are also complete. Validation: 45 Python tests, 6 JavaScript tests, real-model
artifact replay, and desktop/mobile Chrome interaction checks. The refreshed
dashboard uses a backup of the local dataset; the original database was not
migrated by this implementation session.

Next: P2 frozen forward evaluation and P3 calibration, then a local relationship
graph (D5/G1). Clustering remains experimental work requiring validation.

When taking an item, record status and implementation/result links in its
canonical page. Check it off only after its completion criteria are met. Keep
research results tied to the dataset, revision, method, and limitations.
