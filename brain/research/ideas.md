---
summary: Potential improvements discovered while implementing prediction history and the dashboard.
status: maintained
reviewed: 2026-09-11
---

# Ideas discovered during implementation

Parent: [research](index.md). Related: [prediction research](predictions.md),
[groupings](groupings.md), [dashboard TODOs](../tasks/dashboard.md).

Captured at the user's request. These are proposals; inclusion does not imply
they have been validated or implemented.

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
- [ ] **Chain coverage versus export freshness.** Show the timestamp of the last scanned block separately from the export timestamp. The publication audit found that this snapshot ends at block 58,900 (2014-05-27), although its dashboard export was produced in 2026. A fresh export must not imply modern-chain coverage or transfer of historical model results to current transactions.
- [ ] **Deployment manifest.** Record the deployed commit and hashes of HTML, JavaScript, CSS, and JSON. Check the live site's asset hashes after Pages finishes building so a successful build cannot mask a stale cache or mismatched data and interface versions.
- [ ] **Collection completeness watermark.** Record completion per block and audit old scans for missing transaction bodies. A contiguous range of stored heights alone cannot establish that legacy ingestion captured every transaction.
- [ ] **Reorganization recovery.** The collector checks a confirmed tail hash and pauses on mismatch. Design rollback/replay of affected transactions, resolutions, predictions, and lineage without rewriting historical claims as if they never existed.
- [ ] **RPC provenance and agreement.** Record node identity and independently compare block hashes or sampled transactions across nodes. Structural RPC validation does not replace consensus validation.
- [ ] **Incremental cloud analysis.** Separate continuous ingestion from incremental graph updates and periodic model training so catch-up work does not rebuild the full graph twice per export cycle. Measure memory/latency as the dataset grows before increasing VM limits.

Implementation observations behind these proposals: [prediction storage](../../models.py),
[scorer](../../scorer.py), [evidence queries](../../brain.py),
[bounded exports](../../dashboard_export.py). Keep fresh observations here and
promote an idea into the prioritized backlog once its scope and evaluation are clear.
