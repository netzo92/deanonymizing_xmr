---
summary: Potential improvements discovered while implementing prediction history and the dashboard.
status: maintained
reviewed: 2026-09-11
---

# Ideas discovered during implementation

Parent: [research](index.md). Related: [prediction research](predictions.md),
[groupings](groupings.md), [dashboard TODOs](../tasks/dashboard.md).

Captured at the user's request. Unchecked items are proposals; inclusion does not
imply they have been validated or implemented. Completed items name their evidence.

- [ ] **Prediction changes across runs.** Show when a ring's chosen output changes, the score margin, and eventual outcomes. Immutable runs make it possible to distinguish stable predictions from repeatedly changing guesses. Test whether instability predicts errors before using it as a confidence feature.
- [ ] **Verification-label drift.** A later stored deterministic claim can differ from the claim that originally verified a prediction. Preserve the old verification event and flag the discrepancy instead of silently rewriting history. Link both source resolution events and evaluate how often this happens.
- [ ] **Dataset lineage versus snapshot identity.** Database copies should share a lineage ID, but copies can diverge. Add a reproducible snapshot fingerprint and parent-snapshot relation so two runs at the same maximum block height are not assumed to use identical data.
- [ ] **Unscorable rings as a coverage category.** Distinguish accepted, below-threshold, and unable-to-score rings. Missing transaction heights or features should remain visible; they must not disappear from the coverage denominator.
- [ ] **Correlated-error stress tests.** Test calibration and grouping on connected ring components and deliberately shared decoy pools. A high per-ring score may not imply that every selected candidate in a transaction is correct.
- [ ] **Cluster stability timeline.** Once grouping is validated, show group merges, splits, and weak bridge edges across runs. Evaluate stability under added blocks and small score-threshold changes before interpreting a persistent group as meaningful.
- [ ] **Export coverage and on-demand shards.** Bounded exports keep the static site usable, but newest-first samples may hide older outcomes. Add an explicit cohort manifest and optional shards by run/outcome/height; retain exact dataset-wide denominators alongside subset filters.
- [ ] **Scalable evidence retrieval.** Benchmark indexed output lookups and batched explanations across hundreds of thousands of resolutions. Share repeated evidence between historical records for the same ring to shrink export files without losing run-specific scores.
- [ ] **Prediction-time label eligibility.** Record which deterministic labels and exclusions existed before a run. Saved model artifacts and candidate features permit score replay, but later verification at a higher height does not prove that its answer was unavailable at prediction time. Add an audit for that distinction to forward evaluation.
- [ ] **Historical feature provenance.** Preserve training-ring identities and split membership alongside the training-data fingerprint. This would allow an experiment reviewer to reproduce label selection and split decisions, in addition to replaying the saved inference model.
- [x] **Chain coverage versus export freshness.** The cloud export and dashboard now show the last scanned block timestamp, export time, and observed node lag separately. The first cloud snapshot ends at block 59,900 (2014-05-28), although exported in 2026. Verified against the stored tail and in Chrome. A fresh export must not imply modern-chain coverage or transfer of historical model results to current transactions; see [cloud validation](../workflows/cloud.md).
- [ ] **Deployment manifest.** The cloud release now records its source commit, and publication checks compare live asset hashes. Remaining: persist a manifest of HTML, JavaScript, CSS, and JSON hashes, including independently changing data exports. A successful build should not mask a stale cache or mismatched data and interface versions.
- [ ] **Collection completeness watermark.** Record completion per block and audit old scans for missing transaction bodies. A contiguous range of stored heights alone cannot establish that legacy ingestion captured every transaction.
- [ ] **Reorganization recovery.** The collector checks a confirmed tail hash and pauses on mismatch. Design rollback/replay of affected transactions, resolutions, predictions, and lineage without rewriting historical claims as if they never existed.
- [ ] **RPC provenance and agreement.** Record node identity and independently compare block hashes or sampled transactions across nodes. Structural RPC validation does not replace consensus validation.
- [ ] **Batched scoring and incremental cloud analysis.** Batch holdout predictions and unresolved-ring scoring with bounded feature arrays; reuse the existing Analyzer for export instead of rebuilding the graph twice per cycle. Check score equivalence on a fixed snapshot and model. Separate continuous ingestion from incremental graph updates and periodic training as the dataset grows.
- [ ] **Resource metrics and ML failure isolation.** Record elapsed time, peak memory, candidate counts, and bytes written separately for scanning, analysis, training, scoring, and export. Budget training size/tree complexity from those measurements. Isolate ML work so a failed or oversized prediction run cannot repeatedly block cycle advancement and publication of newly collected data.
- [ ] **Web updates without interrupting collection.** A documentation/UI release currently restarts the collector along with the source release. Separate static publication from runtime upgrades, publish asset sets atomically, and expose the UI and collector revisions independently. Test asset rollback and snapshot compatibility while a long scan is running; individual block writes must remain atomic.
- [ ] **Private node and prospective mempool experiments.** Evaluate a private `monerod` as the collector's source and record transaction-pool `receive_time`, local observation times, fee/weight, and eventual confirmation height. The [pool RPC](https://docs.getmonero.org/rpc-library/monerod-rpc/#get_transaction_pool) exposes when that node first received a transaction; it is not a global arrival timestamp. Hypothesis: these observations improve confirmation-delay forecasts or contextual features in forward evaluation. Measure polling gaps and node restarts; historical blocks cannot reconstruct observations that were never collected. These timings do not establish senders or address ownership. Plan SSD capacity and synchronization for the chosen full/pruned configuration using the [official node setup guidance](https://docs.getmonero.org/running-node/monerod-systemd/).

Implementation observations behind these proposals: [prediction storage](../../models.py),
[scorer](../../scorer.py), [evidence queries](../../brain.py),
[bounded exports](../../dashboard_export.py), [collector](../../collector.py). Keep fresh observations here and
promote an idea into the prioritized backlog once its scope and evaluation are clear.
