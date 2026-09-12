---
summary: A 40-output RPC audit found usable origin heights and constant legacy gamma features.
status: maintained
reviewed: 2026-09-11
---

# Output-origin audit: a measured legacy feature limitation

Parent: [research](index.md). Related: [heuristic experiments](heuristic-experiments.md),
[protocol limits](protocol-limits.md), [evaluation](evaluation.md).

Sources: [current audit script](../../research/output_origin_audit.py),
[exact executed version 1](../../research/output_origin_audit_v1.py),
[saved requests, responses, sample and results](../../research/results/output_origins_2026-09-11.json),
[scorer implementation](../../scorer.py),
[official output RPC documentation](https://docs.getmonero.org/rpc-library/monerod-rpc/#get_outs).

## Finding

All 40 sampled `(amount, index)` references returned creation heights and creating
transaction IDs. None of the returned heights exceeded the earliest referencing
input height stored in the source database. In the selected ring contexts, actual
ages ranged from **15 to 41,150 blocks**, with median **3,857.5 blocks**.

Nevertheless, all 40 contexts produced exactly the same three gamma features:

| Scorer feature | Observed value in every sampled context |
| --- | --- |
| `gamma_decoy_log_likelihood` | `-7.495541943884256` |
| `gamma_recent_window` | `1.0` |
| `gamma_age_surprisal` | `0.14991083887768514` |

This collapse follows directly from the legacy branch in
`RingScorer._gamma_decoy_features()`. For nonzero amounts it maps ring-relative
index position to a value between 0 and 15, multiplies by 120, then always takes
the `<= 15 * 120` recent-window branch. That branch returns constants. The source
database contains **zero amount-zero membership rows**, so its legacy population
cannot obtain within-population information from these three features as written.
Other age-rank, reuse, and legacy features can still vary.

The fallback is a relative-position proxy, not measured age. Although 39 of the
40 sampled contexts had actual ages above 15 blocks, this is **not a calibrated
recent/old misclassification rate**, and no proxy-versus-age MAE is reported.
The measured block ages are not converted into elapsed wall-clock time.
The audit shows usable origin metadata and a constant-feature problem; **it
measures no prediction-accuracy gain** from replacing or removing features.

## Exact sample and provenance

Executed on 2026-09-11 in America/Los_Angeles, from
`2026-09-12T00:13:22.693046+00:00` through
`2026-09-12T00:13:49.395101+00:00` UTC. RPC work took **17.226 seconds**:
four batches of ten outputs, one-second pauses between batches, no retries,
and a configured 90-second RPC budget.

The node was `http://xmr-node.cakewallet.com:18081`, using `/get_outs` with
`get_txid=true` and each output's actual amount and index. All four responses
reported `status=OK` and `untrusted=false`. This flag is the node's assertion;
one unauthenticated HTTP endpoint does not provide independent chain validation.
The request order was used to associate returned outputs with identities.

| Referencing-ring stratum | Distinct output identities in sampling frame | Sampled | Selected-context age: min / median / max blocks |
| --- | --- | --- | --- |
| Stored deterministic: `confidence=1.0`, `resolved_at_pass>=0` | 561,820 | 20 | 15 / 827.5 / 30,551 |
| Unresolved: no row in `resolved_spends` | 291,878 | 20 | 178 / 7,514 / 41,150 |

Within each stratum, enumerate distinct `(amount, index)` pairs and retain the
20 lowest SHA-256 values of the UTF-8 string
`20260911:category:amount:index`, where category is `deterministic_ring` or
`unresolved_ring`. For each selected output, choose the lexicographically first
referencing key image within that stratum to obtain its ring context. The two
samples happened to contain **40 distinct outputs**, with no cross-stratum
duplicates. This samples output references, not a representative set of rings,
transactions, wallets, or real spends. Each frame includes possible decoys.

Source: the original `monero_analysis.db`, 58,901 blocks at heights 0–58,900.
Its tail hash was
`b075843a91fae3a3a9a27629503a45f9316ef0b3ecdc31f87e8a1a977e61caa9`.
No schema migration or analysis was run. The script uses SQLite URI `mode=ro`,
`PRAGMA query_only=ON`, and a read transaction; it never instantiates the
repository's database wrapper. SHA-256 was identical before and after:

```text
database: 80e3dfd2cc18e4201b863590cbe5e76efa23e3bbc4759b0be0f6083c1b8535d2
scorer.py: 1505277b4a79879fdcc7d1dc42e37075911716f7fa66ffd8eacd1389b78e998b
executed audit script v1: 98d87ab68db67d5f1f6fd8c12c35298fb99a0fce85d3f2d4df6130721ca8c17e
git HEAD: dde2fa5cdff7b895f12ab17c81440717c15ff0bb
```

The audit script/results were new working-tree files when run; their file hashes
identify the actual versions independently of Git HEAD. Other concurrent
dashboard/documentation edits were not inputs to this measurement. The saved
artifact contains only the sampled public-chain references, RPC responses,
aggregate counts, hashes, and audit provenance; it contains no database copy.

The v1 fingerprint covers the main SQLite file only; it did not record a WAL
fingerprint. The read transaction fixes the rows observed during sampling, but
a main-file hash alone cannot identify a logical snapshot if a nonempty WAL is
present. The exact v1 source and original JSON remain preserved. The reviewed
v2 script records main-file and WAL hashes before and after, flags changes to
either, rejects database/sidecar output aliases, and rejects ambiguous input
contexts. Reproducible v2 results require `persistent_source_files_unchanged=true`.
The v2 runner closes SQLite explicitly and reads release provenance from a valid
`REVISION` file, then Git, or records `unknown` when neither is available.
SQLite shared-memory coordination state is intentionally excluded from this
logical-data fingerprint; read-only connections can still update that state.

Reproduce against the same database bytes and scorer version:

```bash
venv/bin/python research/output_origin_audit.py \
  --db monero_analysis.db \
  --node http://xmr-node.cakewallet.com:18081 \
  --seed 20260911 --per-stratum 20 --rpc-budget-seconds 90 \
  --output research/results/output_origins_rerun.json
```

The node's responses and receipt times can change; compare identity selection,
origin heights, source hashes, and summary counts before comparing runs. This
small historical sample does not establish modern-chain coverage, correctness
of stored resolution labels, or common ownership of any outputs.

## Independent review and tests

[Thirteen focused tests](../../tests/test_output_origin_audit.py) passed without
public-node requests. They verify seeded distinct-identity sampling, exact
integers above JavaScript's safe range, stratum/context selection, legacy proxy
collapse, whole-batch RPC rejection, the request budget, visible age conflicts
and missing outputs, WAL fingerprints, read-only schema preservation, and output
alias protection. Replaying the saved responses reproduces all 40 original
sample rows and the exact saved summary; the preserved v1 source matches the
recorded script SHA-256.

```bash
venv/bin/python -m unittest discover -s tests -p 'test_output_origin_audit.py' -v
```

## Follow-up TODOs

- [ ] **EA1 — Constant-feature audit (P0; supported by this finding).** Inputs: frozen feature matrices and protocol/ring-size cohorts. Baseline: the current 24-feature scorer. Report distinct values, missingness, and variance for every feature per cohort, including cohort sizes. Success: automatically identify these three legacy constants and other zero-information columns, with results linked to a feature version. Failure modes: confusing cohort-constant features with globally useless features or claiming that dropping a constant must improve a retrained random forest.
- [ ] **EA2 — Versioned origin-age features and ablation (P1; depends on HE1/HE3).** Inputs: validated creation heights, immutable prediction-time contexts, explicit missing-origin flags, and frozen temporal splits. Baselines: current proxy features and simple actual-age rank. Introduce a new feature version without rewriting old scores/artifacts. Success: compare top-1 accuracy and precision at matched coverage across declared cohorts, with sample counts and uncertainty; retain a negative result. Failure modes: applying modern decoy assumptions to 2014 data, leaking later graph state, or hiding incomplete joins. No gain is assumed from this audit.
