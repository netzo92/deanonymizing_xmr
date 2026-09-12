---
summary: Prospective node measurements for reliable ingestion and testable confirmation forecasts.
status: maintained
reviewed: 2026-09-11
---

# Private-node observations

Parent: [research](index.md). Related: [cloud workflow](../workflows/cloud.md),
[measurable experiments](heuristic-experiments.md), [protocol limits](protocol-limits.md).

Sources for implemented behavior: [collector](../../collector.py),
[RPC client](../../monero_rpc.py), [cloud service](../../deploy/gcp/collector.service).
The deployed collector queries an external RPC node. A private validating node
and prospective transaction-pool recorder are proposed additions, not installed
capabilities described by this page.

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

## Proposed observation record

Store a node/version identifier, network and tip hash, transaction hash,
observation UTC and monotonic time, node `receive_time`, first/last observation,
fee/weight, pool state, poll success/duration, and eventual confirmation
height/hash. Add a new observer-session ID after restart. Record clock offsets,
poll gaps, and late discovery instead of silently backfilling arrival times.

Keep raw observations in durable storage outside `/var/www/xmr`. Publish bounded
aggregates and research cohort coverage. Wallet secrets and peer-IP attribution
are not inputs to these experiments. RPC configuration should remain private;
the [daemon reference](https://docs.getmonero.org/interacting/monerod-reference/)
documents bind, restricted-RPC, synchronization, and pruning controls.

## Prioritized TODOs

### PN1 — Validating-node pilot (P1; infrastructure proposal)

- [ ] **PN1 — Validating-node pilot (P1).** Inputs: an explicit daemon release, full/pruned storage choice, available SSD/RAM, sync progress, RPC latency/errors, and sampled block/output joins. Baseline: the current external RPC collector.

Success: after synchronization, sampled tail hashes and origin joins reconcile
with independent observations; record blocks/hour, request failure rate, disk
growth, and memory before choosing a permanent configuration. Check every needed
RPC against the chosen pruning mode. Keep the daemon's data directory separate
from the research SQLite database and size it independently of the current
100 GB collector allocation.

Failure modes: bootstrap responses masquerading as local validation, disk
exhaustion, insufficient retained data for an experiment, and assuming node
agreement proves independence. **Evidence status:** official operational
capability; resource needs and benefit for this workload remain unmeasured.

### PN2 — Observation completeness pilot (P1; depends on PN1)

- [ ] **PN2 — Observation completeness pilot (P1).** Inputs: pool snapshots, poll timing, observer sessions, chain confirmations, and a small controlled-transaction cohort. Baseline: confirmed-block timestamps alone.

Success: report the fraction of subsequently confirmed transactions observed
before confirmation, latency for controlled submissions, polling-gap rate,
restart recovery, duplicate rate, and bytes per observation. Preserve transactions
still unconfirmed at the study boundary as censored observations. A proposed
short pilot establishes feasibility before continuous retention is enabled.

Failure modes: losing short-lived pool entries between polls, time drift,
reappearing transactions, or interpreting "not observed" as "not broadcast."
**Evidence status:** prospective design; the current collector stores no pool
observations and provides no historical arrival dataset.

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
hypothesis, with no claimed improvement. A useful display would show the forecast
range, observation quality, realized delay, and baseline side by side.
