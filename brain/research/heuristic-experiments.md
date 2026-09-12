---
summary: Concrete experiments for output ages, temporal evaluation, and grouping with falsifiable baselines.
status: maintained
reviewed: 2026-09-11
---

# Measurable heuristic experiments

Parent: [research](index.md). Related: [protocol limits](protocol-limits.md),
[prediction plan](predictions.md), [groupings](groupings.md),
[evaluation](evaluation.md), [node observations](private-node-observations.md).

Sources for current behavior: [scorer](../../scorer.py),
[RPC client](../../monero_rpc.py), [scanner](../../scanner.py),
[evidence graph](../../brain.py). All experiments below are proposals;
no improvement or address-ownership result has been demonstrated here.

## Current data gap

The scorer estimates ages from output indices and snapshot-wide rates. Its
legacy fallback measures relative position inside a ring. These are proxies,
not measured output creation ages. The scanner stores ring references but not
a complete table of creating outputs, and the existing `get_outs()` helper
hardcodes `amount=0`; it cannot safely resolve every legacy `(amount, index)`.

The official [output RPC](https://docs.getmonero.org/rpc-library/monerod-rpc/#get_outs)
accepts both amount and index, with `get_txid` for creating transaction IDs.
[Transaction RPC](https://docs.getmonero.org/rpc-library/monerod-rpc/#get_transactions)
provides output indices. These support a proposed origin join; they do not reveal
the recipient's published address or the real spend in an arbitrary ring.

The [40-output origin audit](output-origin-audit.md) resolved every sampled
historical reference and found three constant legacy gamma features. This is
bounded feasibility evidence for HE1 and a concrete reason for feature ablation;
it does not complete origin ingestion or demonstrate a prediction gain.

## Prioritized TODOs

### HE1 — Accurate output-origin joins (P0; extends G5)

- [ ] **HE1 — Output-origin joins (P0).** Inputs: `(amount, index)`, creating transaction/hash, output position, block height/hash, unlock metadata, and RPC provenance, including miner outputs. Validate pair identities within one network/dataset; keep test networks separate.

Baseline: today's index-derived age. Success: every reference in a declared
evaluation cohort resolves uniquely or is explicitly marked missing; repeated
joins agree, sampled results agree with an independently validating node, and
no output is created after the input transaction. Measure missing/conflicting
join rates and proxy-age error before training. Use height difference as the
primary age and block timestamp difference as a separate, noisy measure.

Failure modes: merging amount buckets, skipping coinbase outputs, assuming a
partial scan contains every origin, reordered RPC responses, or ignoring a
reorganization. **Evidence status:** missing capability confirmed in source;
its effect on prediction quality remains unmeasured.

### HE2 — Age baselines and feature ablation (P1; depends on HE1/RL1)

- [ ] **HE2 — Age baselines and ablation (P1).** Inputs: origin ages, ring members, protocol cohorts, and labels eligible before each frozen cutoff. Compare uniform random, newest-member, oldest-member, age-only ranking, and the current random forest on the same rings.

Fit any age-distribution or likelihood-ratio model on training/calibration data
only; a low decoy likelihood alone is not a posterior probability of real spend.
Success: report top-1 accuracy and accepted-prediction precision at matched
coverage across predeclared future windows, with ring counts and uncertainty.
Adopt a feature family only when its gain survives removal/addition tests and
the frozen test protocol. Distribution drift is motivated by the
[OSPEAD report](https://web.getmonero.org/2025/04/05/ospead-optimal-ring-signature-research.html),
not evidence that this experiment will succeed.

Failure modes: using current whole-dataset rates for old predictions, testing
only easy verified rings, or mixing legacy and later populations. **Evidence
status:** hypotheses; baselines and exact-age ablations are not implemented.

### HE3 — Temporal and graph separation (P0; extends P2)

- [ ] **HE3 — Temporal and graph separation (P0).** Inputs: immutable prediction runs, training-ring IDs, features frozen at height H, label-eligibility timestamps/heights, and ring-output edges available at H. Baseline: the current random ring holdout.

Use chronological training, calibration, and test windows. Add a sensitivity
test that keeps shared-output/transaction groups from crossing fitting and test
sets; never construct those groups from future edges. Record any giant component
and the fraction of data lost to separation. Success: publish test accuracy,
verified fraction, unscorable fraction, and group-aware uncertainty for both
protocols; a measured performance drop is a useful finding.

Negative controls: randomize the positive label among existing members of each
ring, shuffle candidate order, and compare frozen reuse counts with an explicitly
labeled future-data leakage control. A randomized-label model should approach
its matched chance baseline. Failure modes: indirect graph-label leakage, threshold tuning on the
test set, or purging nearly all difficult data. **Evidence status:** source
confirms random ring splitting; stricter evaluation remains proposed.

The [FA3 follow-up](feature-variation-audit.md#fa3--reuse-tie-comparison-completed)
completed a retrospective prerequisite at 2026-09-12 05:40:55 UTC: strict later
blocks and a sampled pre-cutoff component filter, removing 169 of 645 training
rings. Its feature values still come from the full snapshot and its historical
label eligibility is unknown. This does not close HE3 or establish forward
accuracy; the next step is freezing those available-at-time inputs.

### HE4 — Overlap and co-spend false merges (P2; extends G1–G4)

- [ ] **HE4 — Overlap and co-spend false merges (P2).** Inputs: observed ring-output graph, transactions with multiple input rings, run-specific candidate scores, and separate controlled-wallet spend/ownership labels. Preserve both amounts and indices on every edge.

Baselines: no inferred ownership edges; raw shared-candidate counts; size- and
degree-normalized overlap. Evaluate co-spend candidate selection separately from
common-ownership grouping. Success: on withheld controlled wallets, report
joint input-selection accuracy, pairwise precision/recall, false merges per
1,000 proposed links, largest false merged component, and threshold/seed stability.
Choose thresholds on calibration data and retain competing candidates.

Negative controls: unrelated wallets with similar transaction patterns, popular
decoys, and synthetic graphs preserving ring size and output-degree distributions.
Failure modes: hubs creating spurious communities, correlated ring errors, or a
single weak edge merging many unrelated groups. Never multiply marginal scores
and call the result validated joint confidence. **Evidence status:** proposed;
observed overlap establishes shared candidates, not ownership.

## Result contract

Record source revision, dependency versions, dataset fingerprint, cutoff and
test windows, cohort counts, label provenance, baseline, metric with uncertainty,
coverage, and runtime/memory. Publish failures and abstentions. The display can
then show a baseline comparison, cohort chart, or reversible group proposal
without promoting an unvalidated method into deterministic evidence.
