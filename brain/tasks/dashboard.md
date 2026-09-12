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

- [ ] **D4 — Validated prediction quality panel (P2; depends on P2–P3).** The [analytics lab](../../docs/research-analytics.js) now implements threshold retention, verified-only outcomes and Wilson intervals, descriptive score bins, ring-size/run/provenance cohorts, candidate gaps, reduction, and aggregate downloads. These describe the bounded exported subset; they do not establish calibration or unbiased accuracy. Remaining: frozen forward cohorts, independent evaluation, validated calibration, and time-period comparisons. Pending cases stay outside outcome denominators. See [analytics tests](../../tests/test_research_analytics.js) and the [workspace guide](../workflows/research-workspace.md).

- [x] **D5 — Local relationship graph (P2).** The selected prediction now opens a bounded graph with distinct transaction-input, candidate-membership, overlap, deterministic-claim, current-hypothesis, and historical-prediction edges. Includes a legend, hypothesis toggle, adjustable candidate limit, exact identity details, keyboard selection, and a table alternative. Implemented in [evidence-graph.js](../../docs/evidence-graph.js) with [tests](../../tests/test_evidence_graph.js). Shared membership is explicitly not established ownership. G1 normalized overlap research and cluster overlays after G3 validation remain separate experiments.

- [x] **D6 — Usability and export robustness (P1).** Add narrow-screen layouts, keyboard-accessible controls, chart table alternatives, loading/error/empty states, and an export schema version. Done when small-screen users can read and filter results and old/partial exports fail gracefully. Implemented responsive layouts, chart tables, safe DOM text rendering, and distinct load failures/retry. Chrome checks passed at 1440px and 390px, including keyboard inspection, filters/paging, legacy data, missing charts, malformed JSON, and HTTP errors.

- [x] **D7 — Visual Markdown brain and research TODO browser (P1).** Main-page navigation opens a colored hierarchy with optional cross-references, searchable notes, branch filters, Open/Completed/All tasks, and a source-linked Markdown reader. The [builder](../../brain_export.py) validates repository links and root reachability; the [viewer](../../docs/brain-view.js) supports keyboard and mobile navigation. [Builder tests](../../tests/test_brain_export.py), [viewer tests](../../tests/test_brain_view.js), and Chrome checks cover extraction, safe rendering, navigation, error/retry, and narrow-screen layout. Update the Markdown and rebuild the export when knowledge changes.

Current workspace: overview and scope → analytics lab → prediction browser and
selected evidence graph → dataset charts → research brain. Main-page links jump
directly to each workspace. Keep holdout and forward verification distinct.
Group labels should reflect the evidence; see [grouping research](../research/groupings.md).
Dependencies P1–P3 are defined in [prediction research](../research/predictions.md).

Completed browser work lives in [dashboard.js](../../docs/dashboard.js) and
[dashboard.css](../../docs/dashboard.css), with [JS checks](../../tests/test_dashboard.js).
The checked items were validated against the exported local dataset and synthetic
fixtures. Descriptive analytics are implemented; D4's validated evaluation remains
open. Follow the [publication checks](../workflows/research-workspace.md) after
every webpage update.
