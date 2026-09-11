---
summary: Dashboard TODOs with data dependencies and acceptance criteria.
status: maintained
reviewed: 2026-09-11
---

# Dashboard TODOs

Parent: [TODOs](index.md).

Sources: [dashboard](../../docs/index.html), [export handler](../../main.py),
[analyzer statistics](../../analyzer.py), [evidence API](../../brain.py).
The display now includes aggregate counts, ring-size distributions, history,
verification totals, and a prediction/evidence browser. Unchecked items remain
proposals; completed items below link to their implementation and validation.

- [x] **D1 — Separate evidence classes (P0).** Display deterministic and hypothesis resolutions separately, then reduced and unchanged unresolved rings. Show conflicts as overlapping diagnostic flags. Include scan range, dataset/run identity, and export time. Done when counts reconcile on empty, deterministic-only, and mixed fixtures and each percentage names its denominator. Implemented by [dashboard_export.py](../../dashboard_export.py) and validated in [export tests](../../tests/test_dashboard_export.py). The compatibility `fully_resolved` field still includes hypotheses; the new categories distinguish them and exclude orphan claims from their denominator.

- [x] **D2 — Prediction browser (P1; depends on P1).** Add a searchable, paginated table of key image, transaction, predicted `(amount, index)`, model score, prediction run/height, and pending/correct/wrong outcome. Filter by score, height, ring size, and outcome. Done when selecting a row opens its evidence and bounded/sharded exports clearly state their coverage. Missing metadata must display as unknown.

- [x] **D3 — Evidence inspector (P1).** Show original candidates, current eliminations, prediction, and recorded dependency ancestry. Distinguish current explanations from historical traces. Expose conflicts, legacy origins, missing history, and truncation. Done when a user can follow a claim to its supporting events and see whether hypotheses contributed. Export bounded `Brain` details; GitHub Pages cannot query local SQLite.

- [ ] **D4 — Prediction quality panel (P2; depends on P1–P3).** Show accuracy versus coverage, calibration bins with sample counts, and outcomes by prediction cohort/ring size/period. Include verified fraction, uncertainty intervals, and clear pending counts. Done when uncalibrated scores are labeled, pending cases do not enter outcome denominators, and small/empty cohorts render correctly. Current verification exports are aggregate only.

- [ ] **D5 — Local relationship graph (P2; depends on G1).** Start from a selected transaction/ring with a limited neighborhood. Give transaction-input links, ring-candidate links, deterministic spend claims, and predicted edges distinct styles and a legend. Allow hypotheses to be hidden. Done when graph limits are visible and shared ring membership never appears as established ownership. Add experimental cluster overlays after G3 validation.

- [x] **D6 — Usability and export robustness (P1).** Add narrow-screen layouts, keyboard-accessible controls, chart table alternatives, loading/error/empty states, and an export schema version. Done when small-screen users can read and filter results and old/partial exports fail gracefully. Implemented responsive layouts, chart tables, safe DOM text rendering, and distinct load failures/retry. Chrome checks passed at 1440px and 390px, including keyboard inspection, filters/paging, legacy data, missing charts, malformed JSON, and HTTP errors.

Proposed layout: summary and scope → prediction table → selected evidence/local
graph → evaluation charts. Keep holdout and forward verification distinct.
Group labels should reflect the evidence; see [grouping research](../research/groupings.md).
Dependencies P1–P3 are defined in [prediction research](../research/predictions.md).

Completed browser work lives in [dashboard.js](../../docs/dashboard.js) and
[dashboard.css](../../docs/dashboard.css), with [JS checks](../../tests/test_dashboard.js).
The checked items were validated against the exported local dataset and synthetic
fixtures. D4 and D5 remain future work; the inspector currently lists related rings
and ancestry rather than drawing a relationship graph.
