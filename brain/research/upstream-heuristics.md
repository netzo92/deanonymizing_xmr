---
summary: A pinned Monero source audit found origin-age, decoy-sampling, protocol-cohort and pool-forecast improvements; implementation experiments remain open.
status: maintained
reviewed: 2026-09-11
---

# Heuristics from the Monero implementation

Parent: [research](index.md). Related: [groupings](groupings.md),
[feature variation](feature-variation-audit.md), [private-node observations](private-node-observations.md),
[progress and conclusions](progress-and-conclusions.md).

The official Monero source is now available locally at `references/monero`,
pinned to **v0.18.5.1 / 4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5**.
The [reference guide](../../references/README.md) and
[pin metadata](../../references/monero-source.json) make the checkout reproducible.
It is an implementation reference; no node was built or synchronized. The
checkout is excluded from TraceGrove's git history and deployment archives.

Source inspection is complete for the areas below. This does not establish a
prediction improvement, modern real-spend labels, or wallet ownership. The
current release also cannot establish which wallet algorithm made a 2014 ring.
Detailed audits contain exact commit-pinned source lines:

- [Node metadata and validation](../../research/upstream-node-audit.md).
- [Wallet decoy construction](../../research/upstream-decoy-audit.md).
- [Pool, fee and timing behavior](../../research/upstream-pool-audit.md).

## Most useful findings

| Priority | Source behavior and current gap | Proposed improvement | What must be measured |
| --- | --- | --- | --- |
| First | `get_outs` exposes actual creation height and optional creating transaction ID. Our scorer uses index proxies, and the generic RPC helper hardcodes amount zero. | Cache exact `(amount,index)` origins and use measured block-age features with missingness flags. | Join coverage, positional mapping, source conflicts, feature variation, then a frozen age ablation. |
| First | Wallet selection combines gamma draws, an unlock adjustment, a weighted recent-age branch, output density, block mapping and rejection. Our age-density approximation omits parts of this process. | Build a separately versioned sampler baseline with actual cutoff output distributions. | Distribution replay, eligibility, joint-ring construction, runtime, and paired forward comparison; decoy likelihood is not real-spend probability. |
| First | Rules change by protocol era. The current scorer uses 120 seconds per block; v1's target was 60. | Preserve creation ages in blocks, record protocol/transaction versions, and audit cohorts separately. | Counts and violations by era, amount and original ring size; do not apply modern rules to historical inputs. |
| Next | Current unlock flags omit a separate minimum-age check and do not reconstruct historical validation context. | Add data-integrity diagnostics with historical boundaries and explicit unknowns. | Constraint violations and missing context; never turn eligibility into real-spend labels. |
| Next | Pool fee/weight, backlog and age summaries can support prospective confirmation studies. Receipt/relay metadata has local semantics and restricted-RPC limits. | Start with aggregate forecasting; add own-node transaction observations only with a coverage ledger. | Rolling-origin baselines, censoring, poll gaps, restarts and calibration of confirmation forecasts. |
| Next | Origin transaction IDs give direct co-creation relationships and reveal shared evaluation context. | Add typed output-origin edges and test grouping against a realistic decoy null model. | Join consistency, shared-origin train/test leakage, chance overlaps and false merges. Co-creation does not establish common ownership. |

The node's output-population RPC supports height cutoffs. Restricted public RPC
rejects nonzero-amount distribution requests, so a private node would help our
legacy amount-bucket studies, bulk metadata work and reproducibility. It would
also provide a known software configuration and richer local observation data.
It would not reveal the real member of a valid ring. See the node and pool audits
for query/storage bounds before starting whole-chain distribution work.

## What the real data already establishes

The [reconciliation script](../../research/upstream_context_audit.py) reads only
frozen JSON, with no RPC, database access, training or scoring. Its
[result artifact](../../research/results/upstream_context_2026-09-11.json)
records the exact source hashes and keeps two populations separate:

- **Historical origins:** all 40 recorded ages equal input height minus returned
  creation height. Actual ages range from **15 to 41,150 blocks**, median
  **3,857.5**; **39/40** exceed 15 blocks, while the relative-age proxy stays
  between **0 and 15**. All 40 have the same gamma feature triple. The RPC reports
  all 40 unlocked now, which supplies no historical eligibility distinction.
  This reuses the fixed origin audit, including its documented v1 WAL-fingerprint
  limitation; it is not a new accuracy test.
- **Current observations:** a [frozen live snapshot](../../research/results/upstream_observations_2026-09-11.json)
  contains **13 confirmed blocks**, **231 transactions**, **395 input rings**,
  and **6,320 memberships**. All observed input rings have size **16**. These
  counts reconcile, but there are no actual-spend labels in this feed and the
  upstream source pin does not identify the external node's running version.

The [two bounded public-node probes](../../research/results/upstream_pool_probe_2026-09-11.json)
at 2026-09-12 02:39 UTC also found a restricted, synchronized endpoint serving
aggregate pool statistics: **35 pool transactions** and **79,120 total weight**.
The legacy RPC field is named `bytes_total`; the source accumulates transaction
weight there. This was a one-time capability check. The confirmed-block observer
remains separate from the subsequently implemented [pool pilot](private-node-observations.md).
The source audit explains why individual receipt/relay/backlog timestamps require
separate handling; it is not evidence of forecast improvement.

Neither comparison validates address groupings or a model improvement. The
historical studies, present-day activity, source behavior and future hypotheses
must retain separate denominators and dates.

## Implementation and research TODOs

- [x] **UA1 — Inspect a pinned Monero implementation.** Completed source audits of node metadata/validation, wallet decoy construction and pool timing, with exact upstream links and a reproducible reference checkout. Theoretical conclusion: measured origins, era-specific sampling and prospective timing offer better-grounded inputs. Data conclusion: 39/40 historical sampled ages exceed the proxy's full 15-block range; 395 separately observed modern rings have size 16. This is source/measurement reconciliation, not improved prediction accuracy. Detailed evidence is linked above.
- [ ] **UA2 — Generalize the origin RPC/cache interface (supports HE1/EA2).** Preserve exact amount/index requests and positional responses, request txid explicitly, reject partial/mismatched batches, and cache by network/source/cutoff. Done when legacy and amount-zero fixtures plus sampled real joins reconcile without inventing missing ages. Follow with the existing measured-age ablation rather than counting the plumbing as an accuracy gain.
- [ ] **UA3 — Replay a versioned wallet sampler (supports FA2).** Reconstruct the weighted gamma/recent mixture, correct unlocked-output anchor and density mapping, then add without-replacement and rejection behavior. Keep raw draw likelihood separate from final ring construction. Done when simulations reproduce pinned upstream behavior on fixed distributions and a frozen evaluation reports positive or negative results without changing deterministic labels.
- [ ] **UA4 — Preserve protocol context and audit eligibility.** Store block/transaction versions, creating transaction unlock metadata and coinbase status. Test historical version boundaries, amount exceptions and incomplete timestamp context. Done when valid fixtures remain valid, invalid/missing cases are diagnostics, and modern constraints are not retroactively applied to 2014 rings.
- [ ] **UA5 — Prospective pool forecasting pilot.** Capture aggregate pool count/weight/fee/age and local poll times first; design own-node per-transaction observation/confirmation joins separately. Preserve polling outages and censored pending transactions. Done when rolling-origin simple baselines, coverage and forecast calibration are reported; sender attribution and real-spend inference are outside this outcome. **Partial:** the [pool recorder and display](private-node-observations.md) now support bounded per-transaction sightings and block joins in a separate private database. The controlled cohort, archived evaluation and forward forecasts remain open.
- [ ] **UA6 — Source-aware grouping null model (supports G1/G3/G5).** Add validated origin-transaction edges and simulate ring overlap under versioned candidate selection, amount buckets and output density. Done when chance overlap, shared-origin evaluation dependence and controlled false merges are measured. Keep direct relationships and competing candidate hypotheses reversible.

Reproduce the JSON reconciliation with `python3 research/upstream_context_audit.py`.
Updating a source pin requires rechecking the linked code and audit conclusions.
Keep the original empirical artifacts frozen when new live observations arrive.
