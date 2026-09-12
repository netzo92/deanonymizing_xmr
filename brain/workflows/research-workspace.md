---
summary: How to build, navigate, validate, and publish the visual research workspace.
status: maintained
reviewed: 2026-09-11
---

# Visual research workspace

Parent: [workflows](index.md). Related: [maintenance](maintenance.md),
[dashboard TODOs](../tasks/dashboard.md), [cloud deployment](cloud.md).

The main page links to two different graphs. The research brain visualizes this
Markdown hierarchy and cross-references; the evidence graph visualizes the
bounded neighborhood of a selected prediction. Documentation links are not
transaction evidence. Ring overlap is not a common-ownership claim.

## Knowledge map and TODOs

[brain_export.py](../../brain_export.py) reads Markdown under `brain/`, preserves
its note text, extracts summaries and task checklists, validates local links and
root reachability, and writes [docs/brain.json](../../docs/brain.json). Fenced
examples do not become tasks or graph links. The export includes a content
fingerprint and source-file hashes; a source commit is unknown when brain sources
have uncommitted changes. No database or private workspace files are ingested.

```bash
python3 brain_export.py
python3 brain_export.py --check
```

The [knowledge viewer](../../docs/brain-view.js) provides a colored map, a
searchable Notes view, and an Open/Completed/All TODO view. Select nodes with a
pointer or keyboard, follow note links in the reader, and inspect cited sources.
Mobile devices start with the Notes view; the Map remains available. Markdown
is rendered as safe DOM elements, with raw HTML treated as text.

## Analytics and evidence

The [analytics lab](../../docs/research-analytics.js) explores the included
historical scoring records, not the whole database. It offers:

- Score-threshold retention, saved acceptance decisions, pending counts, and verified-only outcomes.
- Descriptive score bins and Wilson intervals with explicit sample sizes.
- Ring-size, scoring-run, and verification-provenance comparisons.
- Candidate-score gaps only where the stored alternatives are complete.
- Current candidate reduction, deduplicated by ring, and metadata completeness.
- Aggregate CSV/JSON downloads that preserve scope and denominator information.

The exported subset is newest first and may be highly selective. Repeated runs
can include the same ring; verification is selective too. The intervals assume
independent binomial outcomes and do not correct those biases. Descriptive score
bins do not establish calibration. Formal evaluation remains in the
[prediction plan](../research/predictions.md).

The [local evidence graph](../../docs/evidence-graph.js) distinguishes transaction
inputs, candidate membership, observed overlap, current deterministic claims,
current hypotheses, and the selected historical prediction. Hypotheses can be
hidden. Candidate/neighborhood limits and omitted data remain visible; the table
alternative and node details preserve exact `(amount, index)` identities.

## Validation and release

Run the [development checks](development.md), including the focused
[brain export tests](../../tests/test_brain_export.py),
[knowledge-view tests](../../tests/test_brain_view.js),
[analytics tests](../../tests/test_research_analytics.js), and
[evidence-graph tests](../../tests/test_evidence_graph.js).
Serve `docs/` over HTTP and test desktop/mobile navigation, note and TODO filters,
threshold changes, downloads, graph selection, and missing/error data states.
Check the live page after every publication.

The initial workspace release passed the Python and JavaScript suites and local
Chrome checks with the chart CDN blocked. Checks covered the 20-note graph,
source-linked audit reader, TODO status counts, threshold/cohort controls,
aggregate downloads, typed evidence edges, keyboard focus, and mobile layouts.
The refreshed snapshot was the first verified cloud export at height 59,900;
its evidence classes and ring-size distribution reconcile to 664,239 rings.
These checks establish interface/data consistency, not heuristic accuracy.

GitHub Pages serves the committed brain export. The GCP release installer
rebuilds it from the committed Markdown bundle and installs the static assets,
while preserving the collector's latest `data.json`. A research-note update
therefore needs a rebuilt Pages JSON and a GCP release to appear on both sites.
