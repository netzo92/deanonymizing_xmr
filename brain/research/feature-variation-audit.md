---
summary: EA1 measured all 24 current features on 2,000 historical rings, finding four constants and four exact duplicate pairs.
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

- [ ] **FA2 — Fixed-split ablation.** Compare current features with constant/duplicate families removed on the same frozen eligible rings and predeclared split. Record time, memory, candidate scores, and outcome differences; do not assume fewer columns improve accuracy.
- [ ] **FA3 — Reuse tie treatment.** Compare current index-ordered ranks with equal ranks for tied counts, using the same split and explicit chronological/graph controls. Separate genuine reuse information from the index-order signal introduced by ties.
- [ ] **FA4 — Broader feature coverage.** Repeat the audit on an independently validated later snapshot with amount-zero cohorts. Keep absent cohorts visible and check both original-training and surviving-scoring contexts before changing deployed scoring.
