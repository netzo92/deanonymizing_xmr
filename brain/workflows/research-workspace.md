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

## Feature observatory

The [feature observatory](../../docs/feature-audit.js) reads a separate
[frozen aggregate](../../docs/feature-audit.json) from the
[EA1 experiment](../research/feature-variation-audit.md). Its source snapshot
does not change when the live collector exports new blocks. Cohort selection,
feature search, and variation filters expose per-column distinct values,
population variance, missingness, and eligible-ring variation counts. Selected
features show cohort comparisons and exact duplicate columns; CSV exports keep
the cohort and denominator fields.

Whole-audit totals remain separate from selected-cohort counts. Original training
memberships and surviving scoring memberships have different semantics. Constant
columns in this sample do not prove that a feature is globally useless, and
variance is not predictive accuracy. Read the experiment for exact sampling,
source hashes, the compressed matrix, and follow-up ablations.

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

GitHub Pages serves the committed brain and feature-audit exports. Copy the
measured EA1 aggregate to `docs/feature-audit.json` without changing its fields.
The [GCP static publisher](../../deploy/gcp/publish-static.sh) rebuilds the brain
from committed Markdown and installs the static assets while preserving the
running collector and its latest `data.json`. Use `/ui-release.json` to verify
UI source and asset hashes; `/release.json` continues identifying runtime code.
A research-note update needs a rebuilt Pages JSON and static publication to
appear on both sites. Full runtime upgrades still use the release installer.


## Progress, live feed, and results

The main page now links to the resolution timeline and direct relationship
views in [progress-view.js](../../docs/progress-view.js), current-chain
observations in [live-feed.js](../../docs/live-feed.js), and a dedicated
[TODO/results page](../../docs/todos.html). The latter joins canonical Markdown
checklists with the frozen [research progress](../../docs/research-progress.json)
summary; measurements keep their original dataset and selection limits.

History snapshots compare only the same dataset. Net changes in totals are not
counts of individual resolution events. New exports distinguish original
singletons from multi-member deterministic resolutions; older snapshots retain
unknown breakdowns. Direct groups describe shared outputs/transaction inputs,
not wallet or address ownership. The [cloud workflow](cloud.md) describes the
separate current-chain feed and its coverage limits.

Rebuild `brain.json` after note updates, run `node --test tests/test_*.js`, and
check the main and TODO pages on desktop and mobile. Verify automatic refresh
with changed/unchanged data, paused filters, and failed loads. The live observer
JSON is mutable runtime data and is excluded from static UI installation; a
committed copy is only a GitHub Pages snapshot.

## Security-era explorer

The [era page](../../docs/eras.html) compares ten periods from the
[pinned mainnet manifest](../../docs/protocol-eras.json), retaining all sixteen
individual fork boundaries and transition notes. Its timeline, selector and
comparison table separate source-derived implications, analyzed evidence,
recent observed activity, and frozen studies. Read [security eras](../research/security-eras.md)
for the taxonomy and research limits. Selection is shareable through the URL.

New analysis exports contain exact `protocol_eras` counts from the existing
ring-membership scan. Missing or cross-era referencing contexts remain unknown;
the exporter records reconciled evidence totals and the manifest mapping hash.
An older export can be attributed only when its entire explicit scan range fits
one era. Cross-era aggregate counts are never divided into estimated era shares.
Era labels assume mainnet heights; they do not establish recorded block versions,
wallet versions or consensus validity. Absent coverage cannot measure an upgrade's
effect on prediction quality.

The page checks its sources every 60 seconds and retains the last valid source
on failure. Validate it with [era display tests](../../tests/test_era_view.js),
[era export tests](../../tests/test_era_export.py), [manifest tests](../../tests/test_protocol_eras.py),
and desktop/mobile browser checks after publication. Both full and static
deployment allowlists include the page, script, stylesheet and manifest; runtime
upgrades also include [protocol_eras.py](../../protocol_eras.py).


## Dated task activity and experiment outcomes

The TODO page also exposes the Git-derived activity feed and a comprehensive
research ledger. [Task reporting](task-reporting.md) defines first-recorded dates,
observed closures/reopenings, imported completed tasks, local read-state notices,
scientific verdicts and the required publication sequence. The historical
`research-progress.json` cards retain their frozen cohorts; the ledger covers
all research checklists without combining different experiments' denominators.
