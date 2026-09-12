---
summary: Four simple rules compared with current deterministic labels on all 12,076 eligible historical rings.
status: maintained
reviewed: 2026-09-11
---

# Historical heuristic baselines

Parent: [research](index.md). Related: [evaluation](evaluation.md),
[feature variation audit](feature-variation-audit.md),
[output-origin audit](output-origin-audit.md),
[heuristic experiments](heuristic-experiments.md).

Sources: [experiment script](../../research/heuristic_baselines.py),
[complete aggregate results](../../research/results/heuristic_baselines_2026-09-11.json),
[focused tests](../../tests/test_heuristic_baselines.py).

## Measured result

On **12,076 stored deterministic multi-member rings**, choosing a candidate with
minimum original-output reuse had **82.31% expected agreement** with the current
labels. Choosing the highest output index had **78.45% agreement**. The following
figures describe this early-chain, already-resolved population. They do not
measure prediction performance on unresolved rings or modern Monero, and no
model was trained or improved in this experiment.

| Rule | Expected correct selections / 12,076 | Expected agreement | Rings with tied choices | Unique top-choice fraction |
| --- | --- | --- | --- | --- |
| Uniform among original members | 4,168.551551 | 34.52% | 12,076 (100%) | 0% |
| Newest: highest index in amount bucket | 9,474 | 78.45% | 0 | 100% |
| Oldest: lowest index in amount bucket | 1,316 | 10.90% | 0 | 100% |
| Minimum original-output reuse | 9,940.159127 | 82.31% | 2,173 (17.99%) | 82.01% |

Fractional counts are expectations, not observed counts from random guesses.
For a ring with `k` equally preferred candidates, credit is `1/k` if its label is
among them and zero otherwise. Uniform's per-ring credit is `1/n`. Minimum reuse
has a unique preferred candidate in 9,903 rings and agrees with the label in
9,160 of those. Its preferred set contains the label in 11,032 rings overall;
that number is **not** its correct top-1 count. No output-index tiebreak is used.

The exact expected-correct totals are `115552249/27720` for uniform,
`9474/1` for newest, `1316/1` for oldest, and `25049201/2520` for minimum reuse.
Divide each by 12,076 for its agreement rate. The artifact preserves these
rational numerators and denominators, tie-size histograms, unique-choice counts,
and each cohort's denominator.

## Population and method

The source contains 561,820 rows with `confidence=1.0` and
`resolved_at_pass>=0`. Of these, 549,744 original singleton rings were excluded;
all remaining **12,076 rings and 43,511 original candidate rows** were included.
The 39,617 distinct output identities were retained as exact integer
`(amount, index)` pairs. No sampling or floating-point identity conversion was
used. The source has zero amount-zero membership rows.

Eligibility checks reject absent or invalid memberships, mixed amount buckets,
missing or conflicting transaction/input/height contexts, a label outside its
original ring, and an output claimed by multiple deterministic key images.
Each exclusion receives the first matching reason in the script's declared
order, so counts reconcile without overlap. Every rejection category except
singleton had count **zero** in this source. Stored ML hypotheses are ineligible.

Each ring uses its complete original membership, including candidates removed
by later deterministic analysis. Newest and oldest mean greatest and least
index within that ring's single amount bucket; these rules use no RPC creation
height or measured timestamp. Reuse counts the distinct original input key
images referencing each exact output in the **whole snapshot**, including the
evaluated ring itself and rings outside this eligible population. Duplicate
membership rows do not multiply reuse.

## Descriptive cohorts

Eligible rings are sorted by `(input height, transaction hash, key image)` and
assigned quartile `floor(4 * zero_based_rank / N) + 1`. Boundaries can split a
block or transaction, so adjacent height ranges overlap. All four rules use
the same full-snapshot data in every row; these are **not temporal test splits**.

| Quartile | Rings | Input heights | Uniform | Newest | Oldest | Minimum reuse |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 3,019 | 3,995–29,345 | 33.42% | 66.31% | 10.47% | 70.16% |
| 2 | 3,019 | 29,345–48,365 | 29.32% | 68.37% | 21.20% | 74.01% |
| 3 | 3,019 | 48,365–55,251 | 32.64% | 81.58% | 11.03% | 87.22% |
| 4 | 3,019 | 55,251–58,899 | 42.71% | 97.55% | 0.89% | 97.86% |

The artifact also partitions every result by original ring size. Sizes 2, 3,
4, 5, 6, 7, 8, 9, 10, and 11 have respectively 4,873, 2,152, 2,346, 187,
2,117, 24, 5, 2, 8, and 362 rings. Changing ring-size composition affects the
uniform baseline and complicates comparisons across chronological quartiles.
Several size cohorts are too small to support broad conclusions.

## Provenance and reproduction

Run locally on 2026-09-11 in America/Los_Angeles, from
`2026-09-12T01:59:45.619579+00:00` through
`2026-09-12T01:59:48.860844+00:00` UTC. Total recorded runtime was **3.382 seconds**.
The source was the original `monero_analysis.db`, with 58,901 stored blocks at
heights 0–58,900 and tail hash
`b075843a91fae3a3a9a27629503a45f9316ef0b3ecdc31f87e8a1a977e61caa9`.
Eligible input heights were 3,995–58,899.

```text
Git HEAD: acc8ed15c1a406d33ad7b45e50a771892c6599e6
experiment script SHA-256: d065232a0a8c56acdec586b696b8a918acf80204ddacdafede422352b2cdc668
provenance helper SHA-256: 43aaa2f321913f458508deadefe134682fe1e3646e9e7e28e677f5814401f1a3
main DB SHA-256: 80e3dfd2cc18e4201b863590cbe5e76efa23e3bbc4759b0be0f6083c1b8535d2
WAL SHA-256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
WAL bytes: 0
cohort and reuse SHA-256: 88e205552360821dcf91168c38beb055ee6643cff8f2f2dbd67b45023a75b846
```

The script was a new working-tree file when executed; its content hash identifies
the exact experiment independently of Git HEAD. Main-file and WAL fingerprints
were identical before and after. The read transaction uses SQLite `mode=ro` and
`PRAGMA query_only=ON` and closes explicitly. SQLite shared-memory coordination
state is outside the logical-data fingerprint and can change on read-only
connections. No database migration, RPC, or production scorer change was made.
The saved artifact contains aggregate statistics and hashes, not a database copy
or individual wallet claims.

```bash
venv/bin/python research/heuristic_baselines.py \
  --db monero_analysis.db \
  --output research/results/heuristic_baselines_rerun.json \
  --max-seconds 180

venv/bin/python -m unittest discover -s tests -p 'test_heuristic_baselines.py' -v
```

Eight offline tests passed: disjoint eligibility accounting; exact identities
above JavaScript's safe integer range; whole-snapshot, distinct-input reuse;
deadline enforcement; fractional ties; deterministic quartiles; cohort
fingerprints; read-only schema preservation; and exact reconciliation of the
recorded aggregate and cohort numerators. Several checks share a test case.

## Interpretation and next experiment

Current deterministic labels select rings that the existing analysis could
resolve. Agreement on this selected population can be influenced by the
resolution process itself. The database does not record when each historical
label first became available. Reuse includes references from blocks later than
an evaluated input. Related rings are not independent observations. Therefore
these rates support a reproducible retrospective comparison only; no confidence
interval, generalization claim, wallet linkage, or measured ML gain is implied.

The paired feature experiment is tracked as **FA2** in the
[feature-audit TODOs](feature-variation-audit.md). Build a new frozen matrix for
the selected evaluation cohort: EA1's matrix includes only 810 of these 12,076
eligible training rings, so it cannot silently stand in for the full population.
Use fixed grouped splits/seeds and determine constant/duplicate masks from the
training fold only. Compare the unchanged scorer, masked variants, and these
four rules on the same test rings; report paired changes and negative results.
Different candidate contexts or current labels do not establish forward accuracy.
- [ ] **HB2 — Prediction-time reuse baseline (P1; depends on evidence history).** Inputs: immutable snapshots of memberships and labels available at each prediction height. Baseline: minimum reuse with the same fractional tie rule. Success: quantify the difference between full-snapshot and prediction-time reuse under declared graph-aware temporal partitions. Failure modes: using future memberships or later label discoveries; present data cannot establish historical label availability.
