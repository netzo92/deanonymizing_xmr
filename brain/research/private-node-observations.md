---
summary: Prospective node measurements for reliable ingestion and testable confirmation forecasts.
status: maintained
reviewed: 2026-09-11
---

# Private-node observations

Parent: [research](index.md). Related: [cloud workflow](../workflows/cloud.md),
[measurable experiments](heuristic-experiments.md), [protocol limits](protocol-limits.md).

Sources: [pool recorder](../../pool_observer.py),
[recorder tests](../../tests/test_pool_observer.py),
[pool page](../../docs/pool.html), and [deployment guide](../../deploy/gcp/README.md).
The historical collector still queries an external RPC node. The prospective
recorder is implemented as a separate bounded service; its public page identifies
the actual source, freshness, retained scope, and failures. A private validating
node is prepared separately, deferred at the user’s request to keep only the public-RPC pilot.
Neither a synchronized private node nor a forecast improvement is established.

## What a private node adds

Running one's own `monerod` reduces dependence on another operator's RPC data;
the Monero project documents this in [running a node](https://docs.getmonero.org/running-node/).
It also creates an observation point for future transaction-pool activity.
Historical block replay cannot reconstruct observations that were never recorded.

The [pool RPC](https://docs.getmonero.org/rpc-library/monerod-rpc/#get_transaction_pool)
documents transaction hashes, `receive_time`, relay information, fee, and weight.
`receive_time` describes that node's receipt, not universal network arrival or
wallet creation time. A transaction disappearing from the pool is not by itself
proof of confirmation; join it to a subsequently observed block.

Network propagation has its own uncertainty. The original
[Dandelion++ paper](https://arxiv.org/abs/1805.11060) studies anonymity under stated
network/adversary models. A relay observation is not a sender identity label.
The useful initial targets here are collection completeness and confirmation
delay, using measurements from the node we operate.

## Implemented observation record

The recorder stores source/version metadata when available, network and tip hash,
transaction hash, UTC and monotonic observation times, available node `receive_time`
snapshots, first/last sighting, exact fee/weight, pool state, poll duration/outcome,
and observed confirmation height/hash. Every observer restart creates a session;
known daemon start-time changes, polling gaps and wall-clock jumps interrupt
follow-up. An endpoint can be load balanced: endpoint identity is not proof of
one daemon instance. Public-RPC version/start/receipt fields can remain unknown.

The separate private SQLite database lives at
`/var/lib/xmr-pool/pool_observations.db`, outside `/var/www/xmr` and outside both
existing collectors' data stores. Its default retention is a rolling 24 hours,
with a six-hour follow-up horizon, 20,000 tracked transactions, 200,000 sighting
rows and a 256 MiB SQLite cap. This is a feasibility pilot, not a permanent study
archive. Publish only bounded aggregates and research cohort coverage. Wallet secrets and peer-IP attribution
are not inputs to these experiments. RPC configuration should remain private;
the [daemon reference](https://docs.getmonero.org/interacting/monerod-reference/)
documents bind, restricted-RPC, synchronization, and pruning controls.

## What the display can establish

The pool page shows snapshot counts, exact total fees/weights, receipt-time
availability, poll history, gaps, retained transaction states, and block matches.
Confirmed, pending, absent and censored states partition retained transactions;
cold-start and interrupted-follow-up flags overlap those states. Counts across
retained followed blocks have a different rolling denominator.

Displayed delay is elapsed monotonic time from the first local pool fetch to
our later block detection. It includes polling and the configured two-block
follow-up lag. Only same-session, non-cold-start, uninterrupted observations
whose matching block is above the first-sighting tip enter the delay sample.
Pool disappearance alone never supplies a confirmation. The first inventory is
left-truncated; there is no historical arrival reconstruction.

A complete response describes entries returned by that endpoint. It does not
prove we saw all broadcasts or all entries between polls. Restricted public RPC
and unrestricted private RPC have different visibility; source/version/visibility
changes require separate cohorts. Raw timing records are not published.

## First deployed measurement

Public-RPC collection began at **2026-09-12 03:53:47 UTC** on the existing GCP VM,
using recorder commit `5faef380378a94a8964dfaa9a057608f4e1d5c6d` and cohort
`0abc1138f5004c62bc5d4f5d7c8aed75`. The
[frozen aggregate and validation](../../research/results/pool_pilot_2026-09-12.json)
record the **04:10:04 UTC** snapshot:

| Measured population | Count |
| --- | ---: |
| Successful collection rounds / attempted | 17 / 17 |
| Retained tracked transactions | 161 |
| Observed confirmed / still pending / absent / censored | 119 / 11 / 31 / 0 |
| Eligible local detection intervals | 118 |
| Cold-start confirmations excluded from intervals | 1 |
| Transactions across 18 followed blocks | 341 |
| Current returned entries with known / unknown node receipt time | 0 / 11 |

The eligible interval median was **186.8 seconds**, with a 90th percentile of
**487.1 seconds**. These include polling and two-block follow-up lag; they are not
network-wide confirmation latencies. The sample is short, left-truncated and
restricted to observed confirmations. There is no trained forecast or measured
forecast improvement. The result establishes working prospective collection and
block matching on this source. The 31 absent records are not inferred failures.

[Read-only reconciliation](../../research/check_pool_pilot.py) checked 20
conditions against the separate SQLite store, public export and runtime manifest;
all passed. The two existing writer PIDs stayed unchanged, and the pool process
used roughly 22 MiB of memory at the check. Validation also includes 171 Python
project tests, 11 private-node package tests, 84 JavaScript tests, and real Chrome
checks of live observations and mobile/refresh behavior. The binary/node package
was verified but not deployed: the user chose to keep only the public-RPC pilot.
The public page continues updating; this record and the Pages copy are frozen
aggregates, while raw pilot records remain subject to rolling retention.

## Prioritized TODOs

### PN1 — Validating-node pilot (P1; configuration prepared)

- [ ] **PN1 — Validating-node pilot (P1).** Inputs: an explicit daemon release, full/pruned storage choice, available SSD/RAM, sync progress, RPC latency/errors, and sampled block/output joins. Baseline: the current external RPC collector.

Success: after synchronization, sampled tail hashes and origin joins reconcile
with independent observations; record blocks/hour, request failure rate, disk
growth, and memory before choosing a permanent configuration. Check every needed
RPC against the chosen pruning mode. Keep the daemon's data directory separate
from the research SQLite database and size it independently of the current
100 GB collector allocation.

Failure modes: bootstrap responses masquerading as local validation, disk
exhaustion, insufficient retained data for an experiment, and assuming node
agreement proves independence. **Evidence status:** a separate 2-vCPU/8-GB VM,
300-GiB retained pruned data disk, private RPC firewall and signed v0.18.5.1
binary deployment are prepared in the [node package](../../deploy/gcp/private-node/README.md).
Fixed cost would be approximately $85/month plus egress; provisioning was deferred at the user’s request. Synchronization, RPC compatibility, resource measurements and collector
cutover remain open. A successful source/download audit is not a synchronized node.

### PN2 — Observation completeness pilot (P1; recorder implemented, evaluation open)

- [ ] **PN2 — Observation completeness pilot (P1).** Inputs: pool snapshots, poll timing, observer sessions, chain confirmations, and a small controlled-transaction cohort. Baseline: confirmed-block timestamps alone.

Success: report the fraction of subsequently confirmed transactions observed
before confirmation, latency for controlled submissions, polling-gap rate,
restart recovery, duplicate rate, and bytes per observation. Preserve transactions
still unconfirmed at the study boundary as censored observations. A proposed
short pilot establishes feasibility before continuous retention is enabled.

Failure modes: losing short-lived pool entries between polls, time drift,
reappearing transactions, or interpreting "not observed" as "not broadcast."
**Evidence status:** the separate recorder and aggregate display implement
prospective collection, gaps, bounded follow-up and censoring. The public-RPC
pilot can run before PN1, but own-node comparison, a controlled submission cohort,
measured coverage, restart experiments and a frozen evaluation archive remain
open. No controlled transactions have been submitted by this implementation.

### PN3 — Confirmation forecasts (P2; depends on PN2)

- [ ] **PN3 — Confirmation forecasts (P2).** Inputs at prediction time: time since first local observation, fee/weight, pool backlog, recent observed block cadence, and observer coverage. Baselines: next-block prediction, historical median delay, and simple fee/weight bins.

Use chronological training/calibration/test windows. Predict either confirmation
within a stated number of blocks or a delay distribution; do not label a point
estimate as certainty. Success: compare horizon Brier scores and calibration,
delay error on observed confirmations, interval coverage, and retained/censored
sample counts against the baselines on later windows. Report results separately
for continuous observation and outage-affected periods.
Use complete-follow-up horizon cohorts or an explicit censoring model; an unknown
outcome at the study boundary is not a failed forecast.

Failure modes: leaking eventual confirmation into features, dropping unconfirmed
transactions, fitting only unusually fast controlled transactions, and treating
pool eviction as confirmation. **Evidence status:** a testable forecasting
hypothesis, with no claimed improvement. A future forecast display should show the forecast
range, observation quality, realized delay, and baseline side by side. The current
histogram is descriptive observed detection timing, not a forecast evaluation.

## Ideas recorded during implementation

- [ ] **Freeze complete observation cohorts before longer studies.** Export a versioned private archive before rolling retention expires; include pending/censored records, exact feature-availability times, source visibility, software/configuration and clock/poll diagnostics. Choose retention and storage from measured bytes per poll.
- [ ] **Represent time as observation intervals.** First appearance lies between successful polls for continuing observation. Compare interval-aware forecasts with point estimates; keep startup inventory and outages separate.
- [ ] **Compare public and private visibility simultaneously.** During a bounded overlap, compare anonymous aggregate coverage and later block matches with separate cohorts. Do not combine restricted broadcasted views with unrestricted relay-state views into one population.
- [ ] **Ablate node receipt time against local sightings.** After PN2, test whether receipt timestamps add forward forecast value beyond local observation age, fee density and backlog. Preserve zero/redacted values as unknown and record receipt-time changes.
- [ ] **Measure retention and horizon selection bias.** Report delayed confirmations and dropped follow-up separately; repeat with longer frozen windows before making claims about network-wide coverage.
