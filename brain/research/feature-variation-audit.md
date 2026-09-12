---
summary: EA1 feature measurements and registered FA2/FA3 model comparisons, including mixed reuse-tie results and retrospective limits.
status: maintained
reviewed: 2026-09-11
---

# EA1: feature variation in the historical snapshot

Parent: [research](index.md). Related: [output-origin audit](output-origin-audit.md),
[evaluation](evaluation.md), [protocol limits](protocol-limits.md).

Sources: [audit implementation](../../research/feature_variation_audit.py),
[aggregate results](../../research/results/feature_variation_2026-09-11.json),
[exact compressed sample matrix](../../research/results/feature_variation_matrix_2026-09-11.json.gz),
[scorer](../../scorer.py), [focused tests](../../tests/test_feature_variation_audit.py).

## Measured findings

The fixed-seed sample contains **2,000 rings and 14,280 candidate rows**. All
**342,720 feature values** are finite numeric values: none are missing,
nonnumeric, or nonfinite. Four of the 24 feature columns have the same value in
every sampled candidate:

| Feature | Constant value |
| --- | --- |
| `gamma_decoy_log_likelihood` | `-7.495541943884256` |
| `gamma_recent_window` | `1.0` |
| `gamma_age_surprisal` | `0.14991083887768514` |
| `is_legacy_amount` | `1.0` |

The gamma collapse agrees with the earlier [origin audit](output-origin-audit.md):
the nonzero-amount fallback maps relative positions into the recent-window
branch, which returns constants. The legacy flag is expected to be constant in
an entirely legacy population. Neither observation measures an accuracy gain
from changing or removing these features.

Four feature pairs are **exactly numerically equal across all sampled rows**:

| First column | Identical column |
| --- | --- |
| `output_age_rank` | `decoy_likelihood_rank` |
| `is_newest_member` | `is_most_decoy_like` |
| `gamma_recent_window` | `is_legacy_amount` |
| `legacy_triangular_likelihood` | `amount_bucket_progress` |

This is an exact-column audit, not a test of all possible functional dependence
or approximate correlation. The result artifact also reports exact duplicates
inside each sampled stratum; small or narrow strata can create additional
coincidences.

**Global constancy and within-ring constancy differ.** `ring_size` has 47 distinct
values across this sample and zero within-ring variation in all 2,000 rings.
That is expected for a context feature and does not establish that it is useless
to a model. Each feature's artifact entry separately reports finite distinct
count, population variance, and the number of rings with variation. A ring
enters the within-ring denominator only when every candidate value for that
feature is present, numeric, finite, and there are at least two candidates.

**Reuse ties can introduce an age-order signal.** At least two candidates have
equal reuse counts in **1,398 / 2,000 rings**; all candidates have equal counts
in **169 / 2,000**. Nevertheless, `reuse_rank_in_ring` varies inside every
sampled ring because tied reuse counts retain ascending output-index order.
`ring_reuse_count`, `is_min_reuse`, and `reuse_count_raw` vary inside 1,831 rings.
The audit does not establish whether changing the tie rule improves predictions.
Also, `reuse_count_raw` is normalized by the largest count within the ring, and
`distance_from_tx` subtracts an output index from a block height: its name should
not be interpreted as measured elapsed age.

## Sampling and feature context

| Stored status and extraction context | Eligible rings in source | Sampled rings | Sampled candidates |
| --- | --- | --- | --- |
| Deterministic labels; original full rings | 12,076 | 810 | 4,533 |
| No stored resolution; surviving candidates | 64,596 | 1,190 | 9,747 |

The source also contains **549,744 original singleton rings**, excluded because
the current training builder excludes ring sizes below two. They still
contribute to the original graph's reuse counts and persisted eliminations.
There are no stored hypothesis claims in this source. No sampled labels were
inferred from hypotheses, ML scores, or newly run analysis.

The ten nonempty strata combine stored status, amount branch, and original ring
size buckets `2`, `3–4`, `5–8`, `9–16`, `17–32`, and `33+`. Within each stratum,
retain at most 500 rings with the lowest SHA-256 values of
`42:stratum:key_image`. Visit strata in lexicographic round-robin order, consuming
their retained rings in hash order until the 2,000-ring or 20,000-candidate
budget is reached. No ring was skipped for candidate budget in this run. The
entire eligible `33+` unresolved stratum, 180 rings, was included. The artifact
contains exact frame and sampled counts for every stratum.

This deliberately balanced sample is **not weighted to the source population**.
Pooled distinct counts, variances, and tie fractions describe the sample. They
should not be presented as unbiased population estimates.

For deterministic training rings, feature extraction uses all original members.
For unresolved scoring rings, it removes outputs claimed by persisted
resolutions and requires at least two survivors. No new cascade is executed;
an empty or singleton residual would be excluded and counted. None occurred in
this source's eligible unresolved population. Reuse counts are computed from
**every original distinct `(key image, amount, index)` membership** in the full
read snapshot, including the ring itself, exactly matching the lengths consumed
by `RingScorer.extract_features()`. The sample-only graph is never used to
derive features. The two extraction contexts stay separate in stratum summaries.

## Scope and reproducibility

The original database contains 58,901 blocks at heights **0–58,900**, with the
highest stored block timestamp **2014-05-27 13:29:40 UTC**. Its first positive
timestamp is 2014-04-18; one block has timestamp zero. All 1,052,521 membership
rows and all 626,416 rings use nonzero amount buckets. **There are no amount-zero
or modern-chain measurements in this audit.** Feature values use the current
whole-snapshot graph, not a reconstruction of what was known when each
historical transaction occurred. Original resolution-label availability at
historical cutoffs is unknown.

The run started at `2026-09-12T01:58:26.057466+00:00` (September 11 in the local
America/Los_Angeles timezone), took **4.564220 seconds**, and reported peak process
RSS of **467,599,360 bytes**. This is the audit process's memory measurement,
not a production-training capacity benchmark. Runtime versions were Python
3.14.7, NumPy 2.4.3, and SQLite 3.53.4.

The runner opens SQLite with URI `mode=ro`, enables `query_only`, and holds a
read transaction. It does not instantiate `Database`, run `Analyzer`, migrate
schema, request RPC data, fit a model, or update evidence. Persistent source
files have identical before/after SHA-256 values:

```text
database: 80e3dfd2cc18e4201b863590cbe5e76efa23e3bbc4759b0be0f6083c1b8535d2
WAL (zero bytes): e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
scorer.py: 1505277b4a79879fdcc7d1dc42e37075911716f7fa66ffd8eacd1389b78e998b
sampling manifest: 23213ffee006cc05f2229e51bbf3a2036563096281ad77df366b90be05d6bee1
```

Source revision was `acc8ed15c1a406d33ad7b45e50a771892c6599e6`. The result records
the exact audit-script and helper hashes because the new audit files were not
yet committed. SQLite shared-memory coordination state is excluded from the
persistent-data fingerprint; read-only connections may update that state. The
runner rejects changed database/WAL bytes instead of publishing a reproducible
snapshot claim for a changing source.

The gzip sample is **1,521,914 bytes** and contains exact sampled public-chain
identities as decimal strings, original and surviving memberships, contexts,
stored deterministic outputs where eligible, raw feature vectors, and reuse
counts. Its SHA-256 is recorded in the aggregate JSON, alongside the canonical
uncompressed matrix hash. It contains no wallet ownership or sender labels.
Recomputing global features, duplicate pairs, and tie diagnostics from the
frozen matrix reproduced the saved aggregates exactly.

```bash
venv/bin/python research/feature_variation_audit.py \
  --db monero_analysis.db --seed 42 \
  --max-rings 2000 --max-candidates 20000 --per-stratum 500 \
  --output research/results/feature_variation_rerun.json \
  --matrix-output research/results/feature_variation_matrix_rerun.json.gz

venv/bin/python -m unittest discover -s tests -p 'test_feature_variation_audit.py' -v
```

Nine focused tests passed. They cover sample determinism and bounds, full-graph
reuse, extraction contexts, invalid/hypothesis labels, missingness and variance,
legacy constants, identity precision, vector contracts, tail timestamps, schema
and database/WAL preservation, and rejection of a changing source.

## Next experiments

- [x] **FA2 — Fixed-split ablation.** Compare current features with constant/duplicate families removed on the same frozen eligible rings and predeclared split. Record time, memory, candidate scores, and outcome differences; do not assume fewer columns improve accuracy.
- [x] **FA3 — Reuse tie treatment.** Compare current index-ordered ranks with equal ranks for tied counts, using the same split and explicit chronological/graph controls. Completed the registered three-split comparison below; equal ranks gained two correct rings only on the random split. The broader improvement hypothesis remains inconclusive, and prediction-time evaluation remains HE3.
- [ ] **FA4 — Broader feature coverage.** Repeat the audit on an independently validated later snapshot with amount-zero cohorts. Keep absent cohorts visible and check both original-training and surviving-scoring contexts before changing deployed scoring.


## FA2 — Paired ablation completed

The [registered protocol](../../research/feature_ablation_protocol.json) and
[runner](../../research/feature_ablation.py) were committed as
`753fd144fc98a98745e08932b6abcbc7ebafd85b` before fitting. The
[complete result](../../research/results/feature_ablation_2026-09-12.json) finished
at **2026-09-12 05:00:14 UTC**. It reads the frozen EA1 matrix only: no database,
RPC, original-label mutation or live scorer change.

The fixed seed-42 split used 648 training rings and the same 162 held-out rings
(890 candidates) for all variants. Masks were discovered on training rows only.
The 500-tree random forest, leaf size 3 and balanced weights were held constant;
one CPU worker and a fresh process per variant bounded local work.

| Variant | Columns | Agreement with stored labels | Forest fit time | Process peak RSS |
| --- | ---: | ---: | ---: | ---: |
| All features | 24 | 142 / 162 (87.65%) | 1.051 s | 212,303,872 bytes |
| Remove constants | 20 | 142 / 162 (87.65%) | 1.231 s | 210,239,488 bytes |
| Remove constants and exact duplicates | 17 | 142 / 162 (87.65%) | 1.196 s | 208,912,384 bytes |

Each reduced variant changed one selected output on a ring that remained wrong;
there were zero newly correct or newly incorrect rings. Scores, selected outputs,
scaler statistics, exact labels, masks and runtime versions are saved under the
[result directory](../../research/results/feature_ablation_2026-09-12).

**Conclusion:** fewer columns produced no agreement gain in this one test.
The small memory differences and single timings do not establish a repeatable
capacity or speed improvement. All models use the same `max_features=sqrt`
setting; changing dimensionality can itself change forest sampling behavior.

The test is retrospective and selectively labeled. Of 162 test rings, 15 share
an output with training and 100 share a transaction; 24 distinct outputs cross
the split. Full-snapshot features and unknown historical label eligibility remain.
No modern, forward, independent-label or per-feature causal benefit is established.
The broader improvement hypothesis remains inconclusive. Next: HE3 temporal/graph
controls and FA4 validated later-era coverage; the FA3 follow-up is below. The deployed
scorer still uses its existing 24 features.


## FA3 — Reuse tie comparison completed

The [protocol](../../research/reuse_tie_protocol.json),
[runner](../../research/reuse_tie_experiment.py) and
[method tests](../../tests/test_reuse_tie_experiment.py) were committed as
`287922b75c91645a3b6a352ad26efa0f22ba3bea` before any FA3 model fit.
The [complete result](../../research/results/reuse_ties_2026-09-12/summary.json)
finished at **2026-09-12 05:40:55 UTC**. This follow-up reused the frozen EA1
matrix and already-inspected FA2 random split; it is not a new untouched test.
No database, RPC or deployed scorer was changed.

Only `reuse_rank_in_ring` changes. Current ranks give equal counts different
positions in ascending output-index order. The alternative assigns the average
normalized position to all candidates in each tie: all equal counts become
0.5, and untied ranks remain identical. Other reuse and index-position features
remain, so this comparison does not isolate every source of either signal.

Each cohort fits the fixed seed-42, 500-tree forest with leaf size 3, balanced
weights and a training-only scaler. Every variant retains all 24 columns. Six
fresh processes each run one fit with one worker; no tuning or result-based
selection is performed.

| Split | Training rings / candidates | Same test rings / candidates | Index-ordered correct | Equal-rank correct | Paired change |
| --- | ---: | ---: | ---: | ---: | ---: |
| Existing FA2 random split | 648 / 3,643 | 162 / 890 | 142 (87.65%) | 144 (88.89%) | +2 rings (+1.23 percentage points) |
| Earlier training, later blocks | 645 / 3,998 | 165 / 535 | 159 (96.36%) | 159 (96.36%) | 0 |
| Same later blocks, connected training removed | 476 / 2,470 | 165 / 535 | 158 (95.76%) | 158 (95.76%) | 0 |

Agreement means matching selective stored deterministic labels under analyzer
assumptions. The different random and later-block test populations make their
absolute rates unsuitable as evidence of a generalization improvement.
The random baseline reproduced every FA2 baseline candidate score and outcome.
Equal ranks changed three random-test selections: two became correct and one
remained wrong; none became newly wrong. All three changes were among the 116
random test rings with reuse ties. Both later-block comparisons changed zero
selected outputs; each contains 58 test rings with reuse ties. No model produced
an exact top-score tie in these six fits, so fractional tie agreement equals
operational agreement. Unchanged choices do not imply identical scores.

**Chronological and graph controls:** the cutoff is height **55,068**, selected
by the predeclared 80% rule. Training uses strictly earlier heights, with all
rings in the boundary block assigned to test. Components use original output
memberships and same-transaction links from only **1,316 sampled earlier rings**,
including unlabeled bridges. This produces **561 components**, largest **209
rings**. The overlap filter removes **169 / 645 training rings (26.20%)** in
components touched by later labeled test identities; **476 remain**. It removes
all direct sampled output and same-transaction overlap. No at/after-cutoff
unlabeled bridge builds the components, and labels do not define edges.

The test identities inform an offline evaluation filter; this is not an online
address-grouping rule. Missing nonsampled rings may connect the remaining
components. Features still use the full 0–58,900 snapshot and historical label
availability is unknown. Removing training rings also changes sample size and
composition. These controls therefore do not complete HE3, prove independence,
or explain the one-ring difference between chronological baselines causally.

**Conclusion:** equal ranks showed a small gain on one reused random split and
no selection gain on either later-block comparison. The broader improvement
hypothesis is **inconclusive**. There is no forward, modern-chain, calibration,
independent-spend or wallet-ownership finding. The deployed scorer retains its
index-ordered ranks. All scores, split identities, transformed-vector hashes,
scaler statistics, paired subgroups, comparator expectations and resource
observations remain in the result directory.

```bash
venv/bin/python research/reuse_tie_experiment.py run \
  --registered-commit 287922b75c91645a3b6a352ad26efa0f22ba3bea \
  --output-dir research/results/reuse_ties_rerun
```

Use a new result directory: the runner refuses to overwrite an existing study.
Next: freeze feature and label availability before each cutoff (HE3), retain
unverified outcomes, and evaluate independent later-era cohorts (FA4).
A possible diagnostic is label-blind shuffling within tied reuse groups across
predeclared seeds. That preserves the rank range while breaking its connection
to index order, unlike midranks which also reduce within-ring variance. This
idea is untested and must use a fresh declared evaluation window before fitting.
