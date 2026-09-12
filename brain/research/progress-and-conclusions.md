---
summary: Completed research, measured findings, theoretical implications, and the next experiments shown on the dedicated progress page.
status: maintained
reviewed: 2026-09-11
---

# Research progress and conclusions

Parent: [research](index.md). Related: [TODOs](../tasks/index.md),
[evaluation](evaluation.md), [feature variation](feature-variation-audit.md),
[historical baselines](heuristic-baselines.md).

Sources: [dedicated progress page](../../docs/todos.html),
[page implementation](../../docs/todo-results.js),
[structured research snapshot](../../docs/research-progress.json),
[page and artifact-reconciliation tests](../../tests/test_todo_results.js),
[origin results](../../research/results/output_origins_2026-09-11.json),
[feature results](../../research/results/feature_variation_2026-09-11.json),
[baseline results](../../research/results/heuristic_baselines_2026-09-11.json).

## Task history and all research outcomes

The [task-reporting workflow](../workflows/task-reporting.md) now defines the
complete research ledger, Git-derived task dates and website change notices.
The [canonical hypothesis ledger](../../research/hypotheses.json) maps every
research checklist to its question/objective, execution state, verdict,
theoretical and measured conclusions, evidence and remaining work. This includes
open and deferred work; the three historical cards below are not the whole
experimental program. The [pool pilot](private-node-observations.md) adds a
separately dated current-chain feasibility measurement, not a forecast gain.

## What is complete

The dedicated page separates canonical checklist completion from completed
experiments and their interpretation. Checklist status comes from `brain.json`;
the measured summaries are frozen in `research-progress.json`. A completed
implementation or experiment does not mean its associated hypothesis is true.
Open checkboxes remain proposed work, even when their description mentions
completed prerequisites.

These three completed experiments used the original historical database at
heights **0–58,900**, containing **58,901 blocks** and no amount-zero membership
rows. The highest stored block timestamp is **2014-05-27 13:29:40 UTC**. They
are separate from the live collector's later snapshots. The runs occurred on
2026-09-12 UTC, which was September 11 in America/Los_Angeles. Each canonical
experiment page records its exact start time, sample, source hashes, and limits.

| Completed experiment | Measured finding | What the result does not establish |
| --- | --- | --- |
| [Output-origin audit](output-origin-audit.md) | All 40 sampled origins returned metadata. Ages spanned 15–41,150 blocks, while three legacy gamma features stayed constant. | Population coverage, independently validated RPC truth, or a gain from replacing features. The v1 run did not fingerprint WAL state. |
| [EA1 feature variation](feature-variation-audit.md) | On 2,000 rings and 14,280 candidates, four of 24 columns were constant and four pairs were exactly equal. All 342,720 feature values were finite. | A population-weighted estimate, modern-chain coverage, or improved prediction performance from removing columns. |
| [Historical heuristic baselines](heuristic-baselines.md) | Across 12,076 eligible already-resolved multi-member rings, minimum reuse had 82.31% expected agreement with current labels; highest index had 78.45%. | Unbiased accuracy, forward performance, independent ground truth, or performance on unresolved rings. |

The baseline comparison also includes uniform selection at **34.52%** and lowest
index at **10.90%**. Every percentage uses the same 12,076-ring denominator and
original candidates. Ties receive `1/k` expected credit when the label is in the
preferred set; fractional counts are expectations. Current full-snapshot reuse
can include references added after an evaluated input. Chronological quartiles
are descriptive cohorts, not train/test partitions. Related rings are not
independent samples.

The original artifact calls its rate field `expected_accuracy`; the page labels
that quantity **expected agreement** to preserve the experiment's selective,
retrospective interpretation. It does not combine these values with dashboard
holdout or forward-verification figures.

## Theoretical conclusions and proposed tests

The following implications motivate new experiments; they are not additional
measured results.

1. **A simpler feature set deserves a paired ablation.** Constant and duplicate
   columns are reasons to compare masks on the same frozen held-out rings.
   Determine masks from training folds only. Preserve negative findings and
   measure runtime, memory, and prediction changes rather than assuming gains.
   This is [FA2](feature-variation-audit.md).
2. **Origin metadata should enter through a new feature version.** Validate joins
   and missingness, then compare actual-origin features with current proxies
   under the same evaluation boundaries and appropriate protocol assumptions.
   This is [EA2](output-origin-audit.md); the small RPC audit is not that test.
3. **Reuse needs a prediction-time comparison.** The historical lead may depend
   on later observations and which rings the analyzer could resolve. Freeze
   membership and label availability at each cutoff before interpreting forward
   results. This is [HB2](heuristic-baselines.md).
4. **Output relationships require separate ownership validation.** None of the
   completed experiments establishes common wallet ownership, recovers receiving
   addresses, or identifies a person. Hypothesized groups need controlled labels,
   false-merge analysis, and reversible records; see [grouping research](groupings.md).

The page suggests FA2, HB2, EA2, and broader-cohort coverage FA4 in that order.
Their displayed status is looked up from the canonical checklist, so suggestions
cannot silently become completed experiments. A future result should be added
as a separately dated artifact with its population, method, and limitations.

## Reporting ideas discovered while connecting the results

- [ ] **RP1 — Machine-checkable conclusion claims.** Represent each displayed numerical claim with its artifact path, JSON field, denominator, and file hash. The current page tests reconcile its three frozen summaries; extend this to a small claim manifest so later result updates cannot leave stale conclusions. Completion requires a failing check when a source number, denominator, or hash changes without updating its claim.
- [ ] **RP2 — Experiment versus deployment status.** Track a completed study, an accepted implementation change, and a deployed feature version separately. A successful ablation should not appear as a live-model improvement until the selected change and deployment are recorded. Completion requires independently visible study, implementation, and deployment provenance for one controlled example.

## Updating the page

Edit the canonical Markdown checklists for task status and rebuild the public
brain. Update the structured progress snapshot only from saved research artifacts;
retain original experiment dates and source fingerprints. The page reads each
export independently, so a missing result snapshot does not hide available
checklists. All note and artifact content is rendered as text, with restricted
repository source links. Query parameters support completion status, area,
search, and exact task IDs such as `todos.html?status=all&task=FA2#tasks`.

Run `node --test tests/test_todo_results.js`, check Markdown links with
`python3 brain_export.py --check` after rebuilding, and test the webpage after
every update. Follow the [workspace publication checks](../workflows/research-workspace.md).
