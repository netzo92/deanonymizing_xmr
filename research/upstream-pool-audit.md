# Upstream transaction-pool and confirmation-context audit

Reviewed: **2026-09-11 America/Los_Angeles** (probe: 2026-09-12 UTC).
Scope: Monero **v0.18.5.1**, commit
`4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5`, inspected in
`references/monero`. This is a source audit and four prospective research ideas,
not a measured forecasting improvement. The public node's actual software
revision was not established by the two aggregate probes.

Related: [private-node observations](../brain/research/private-node-observations.md),
[evaluation](../brain/research/evaluation.md),
[sanitized probe record](results/upstream_pool_probe_2026-09-11.json).

## Main finding

The strongest immediate addition is **prospective congestion and observation
coverage**, followed by fee/weight confirmation baselines. A restricted public
RPC already exposes useful aggregate pool age, fees and weight. Operating our
own node adds control over validation, version, clocks and collection continuity;
unrestricted local RPC also exposes more pool entries and some per-transaction
timestamps. Neither source supplies a universal first-seen time, sender IP,
wallet identity, or proof that ring members share an owner. These features would
forecast transaction confirmation/context; they do not identify the real input
of a ring or validate address groupings.

## What the pinned source establishes

| Interface or field | Restricted RPC | Unrestricted RPC / interpretation |
| --- | --- | --- |
| `/get_transaction_pool` | Route exists; returns broadcasted-category transactions. Per-entry `receive_time` and `last_relayed_time` are zeroed. Fee, weight and several state flags remain populated. | Includes all relay categories. `receive_time` is local mutable metadata; `last_relayed_time` is still zeroed for stem entries. The response also includes transaction bodies, so it requires explicit byte/request bounds. |
| `/get_transaction_pool_hashes` and `.bin` | Broadcasted-category hashes. | All-category hashes; a different observation population. |
| `/get_transaction_pool_stats` | Broadcasted-category count, weight/fee totals, age histogram, oldest receipt and failure/relay counters. Aggregate ages are not zeroed by the per-entry timestamp rule. | All-category aggregate; do not compare directly with restricted counts without recording the scope. |
| JSON-RPC `get_txpool_backlog` | Registered; default call includes broadcasted entries. | Handler also uses the default broadcasted scope; becoming unrestricted does not automatically change this particular call. Contains weight/fee/time fields but no transaction IDs. |
| JSON-RPC `get_miner_data` | Registered without a restricted-route gate; handler has a core-readiness check. | Returns a filtered, potentially shortened mining backlog with transaction IDs, weight and fee, plus next-height context. This is not a complete pool snapshot. Public operators may independently disable/rate-limit routes. |

Route registration: [core_rpc_server.h:126–150][routes-pool] and
[176–180][routes-backlog]. Pool, hash and stats handlers:
[core_rpc_server.cpp:1650–1742][handlers-pool]. Entry filtering and timestamp
redaction: [tx_pool.cpp:1186–1224][pool-info]. Category membership is defined in
[blockchain_db.cpp:47–75][categories]: broadcasted means block/fluff; it excludes
local/forward/stem and unrelayed `none`. These are local daemon states, not
sender classifications. Upstream bootstrap forwarding and payment checks also
mean route registration alone does not establish a particular public provider's
availability or independently validated data.

Backlog defaults are explicit in the
[RPC handler:3322–3338][backlog-handler] and
[core declaration:507][backlog-default]; miner-backlog scope defaults are in
[tx_pool.h:284–294][miner-default].

Three field traps matter before building a dataset:

1. **Receipt and relay time are not immutable event timestamps.** In
   [tx_pool.cpp:223–244][receive-initial], `receive_time` comes from the node's
   wall clock. [Lines 285–330][receive-upgrade] rewrite it when a relay method is
   upgraded or a transaction is new. `last_relayed_time` can temporarily be a
   sentinel or scheduled future forward/embargo time; the stored field's role
   is explicit in [lines 843–851][relay-schedule] and
   [859–889][relay-update]. Stem timestamps remain redacted in the RPC even
   when unrestricted. Preserve our own first successful observation separately;
   do not substitute a redacted zero, a block timestamp, or a relay deadline.
2. **Stats `bytes_*` are transaction weight in this implementation.**
   [tx_pool.cpp:1099–1133][pool-stats] accumulates `meta.weight`, not serialized
   blob bytes, and computes age aggregates from local `receive_time`. Label the
   dashboard accordingly. `num_failing` counts entries with a recorded failure
   height; it is not a new consensus verdict at every stats poll.
3. **Backlog `time_in_pool` has a source-level arithmetic anomaly.**
   [tx_pool.cpp:1025–1035][raw-backlog] writes `meta.receive_time - now` into the
   `uint64_t time_in_pool` field defined at
   [core_rpc_server_commands_defs.h:1640–1664][backlog-type]. For an ordinary
   older entry, that unsigned value wraps rather than expressing positive
   elapsed seconds. The response uses POD-as-blob serialization and omits txids.
   This is established from the pinned source, **not tested on CakeWallet**.
   Exclude this field from age features until a versioned decoder and controlled
   fixture verify actual wire behavior; never join anonymous backlog entries to
   pool hashes by array position.

## Empirical availability: two bounded public-node requests

At **2026-09-12 02:39:20 UTC**, two sequential POST requests to the project's
configured public origin, `http://xmr-node.cakewallet.com:18081`, each used a
15-second timeout and 1 MiB response ceiling. Only sanitized aggregate values,
response lengths and timing were retained in the
[probe artifact](results/upstream_pool_probe_2026-09-11.json). No full pool
identities, transaction bodies, or peer information were requested or saved.

| Probe | Observed result |
| --- | --- |
| JSON-RPC `get_info` | HTTP 200; `status=OK`, `restricted=true`, `synchronized=true`, `busy_syncing=false`, height **3,760,541**, pool count **35**, weight median **300,000**, weight limit **600,000**, `untrusted=false`; version string empty. Elapsed **0.142 s**. |
| `/get_transaction_pool_stats` | HTTP 200; **35** transactions, `bytes_total=79120`, fee total **9,512,910,000 atomic units**, **10** histogram bins, `oldest=1789180568` (02:36:08 UTC), zero >10-minute, failing, not-relayed and double-spend counters. Elapsed **0.301 s**. |

The public endpoint therefore exposed useful aggregate congestion and receipt-age
information on this occasion. Its `untrusted=false` and synchronized flag are
node-reported fields, not independent validation by TraceGrove. This single
snapshot is neither an availability benchmark nor a delay distribution. The
per-transaction pool interface, timestamp redaction and backlog anomaly were
**not** empirically probed. Our local request duration is client-to-RPC latency,
not peer propagation or wallet submission latency. Two separate requests are
not an atomic node snapshot even though their counts agreed here.

## Gap against today's live observer

TraceGrove source audited at commit
`dd5452bb07f212f6c099e6d159fab1a48874fa2e`:
[live_observer.py:31–43](https://github.com/netzo92/deanonymizing_xmr/blob/dd5452bb07f212f6c099e6d159fab1a48874fa2e/live_observer.py#L31-L43)
stores block summaries, observer metadata and gap records.
[Lines 171–207](https://github.com/netzo92/deanonymizing_xmr/blob/dd5452bb07f212f6c099e6d159fab1a48874fa2e/live_observer.py#L171-L207)
temporarily fetch confirmed transaction hashes/bodies to count inputs and ring
members, then retain aggregates. The
[export at lines 288–327](https://github.com/netzo92/deanonymizing_xmr/blob/dd5452bb07f212f6c099e6d159fab1a48874fa2e/live_observer.py#L288-L327)
explicitly labels `observed_at` as block fetch time.

At this audit revision it persists **no pool snapshots or pool hashes, per-transaction
first/last sightings, fee/weight observations, or transaction-to-confirmation
joins**. Current ring-size counts support activity summaries, not the following
forecasts. Confirmed transaction hashes/fees/weights can be fetched from history
when needed; past node receipt times, failed polls, disappearing entries and
missing pool observations cannot be reconstructed from those blocks. No observer
or collector changes are part of this audit. A subsequent implementation is
documented in the [prospective pool pilot](../brain/research/private-node-observations.md);
its measurements do not alter this frozen probe.

## Prioritized research ideas

### UP1 — Congestion context without transaction identities (P1)

**Code evidence:** restricted pool stats retain age aggregates, fees and weights
([stats implementation][pool-stats]); the two-request probe demonstrates current
aggregate availability. **Hypothesis:** backlog weight relative to the current
weight median, old-entry fraction and recent backlog change may improve a
short-horizon congestion forecast and explain shifts in confirmed activity.

**Prospective inputs:** append bounded snapshots with UTC request start/end,
monotonic duration, session/source identifier, reported version/restriction/
bootstrap state, tip height/hash, each stats value, poll success and explicit
missingness. Record the denominator and collection scope. A first prototype
needs no stored transaction identities. Publish aggregate history and quality
coverage, not a per-transaction delay estimate inferred from aggregate ages.

**Evaluation:** predict the next fixed-time snapshot's pool weight or change;
compare last-value persistence, rolling median and time-of-day baselines on later
time windows. Report MAE by congestion regime, failed-poll fraction, restart
boundaries and effective sample size. Keep outage-spanning targets separate;
never fill failed polls with zero. Block-bootstrap uncertainty intervals account
for strongly correlated adjacent samples. Report source/version changes and
late responses because a provider change can look like sudden pool movement.

### UP2 — Observation coverage and interval-censored first sightings (P1)

**Code evidence:** restricted hash RPC supplies a public-category presence set,
while node receipt metadata is mutable/redacted ([handlers][handlers-pool],
[receipt upgrades][receive-upgrade]). **Hypothesis:** measuring coverage first
will distinguish actual forecasting difficulty from the observer missing
short-lived transactions.

**Prospective inputs:** pool snapshot membership keyed by transaction hash,
first/last successful observer sightings and session, each poll's success/time
window, separately versioned daemon timestamps when available, and explicit
joins to observed block hashes/heights. Record reappearance instead of treating
it as a new transaction. At a session's first poll, existing entries have unknown
prehistory. A previous successful absence followed by presence bounds a local
observation interval; an outage widens that interval. It never bounds global
broadcast time. Keep raw observations outside the public web directory.

**Evaluation:** among all non-miner transactions confirmed in a fixed followed
block window, report the fraction seen beforehand, separately for continuous
polling, outages, startup and reorg periods. Downsample a high-cadence pilot to
measure missed-sighting sensitivity. Preserve late discovery and right-censored
unconfirmed entries; pool disappearance alone is not confirmation. Restrict
comparisons to equivalent public-category views across sources. The private-node
extension offers controlled clocks/version/uptime, not an ownership label.

### UP3 — Fee/weight rank and backlog-ahead baseline (P2; after UP2)

**Code evidence:** entries are keyed by `fee / weight`
([tx_pool.cpp:324–330][receive-upgrade]); the comparator orders descending ratio,
then ascending local receipt time, then transaction hash
([tx_pool.h:63–77][fee-order]). Block construction traverses that order but skips
relay-ineligible/pruned entries, checks weight and reward constraints
([tx_pool.cpp:1558–1632][template-selection]), revalidates inputs, and avoids
already-used key images ([1650–1691][template-ready]). Thus raw rank is not a
guarantee of inclusion or a universal rule followed by every miner.

**Hypothesis:** fee/weight percentile and cumulative observed weight above a
transaction's fee/weight may improve confirmation-within-1/3/6-block forecasts
over a constant next-block prior and age-only baseline. Use transaction weight,
not blob size, and preserve atomic fee integers. Equal-fee ranks on a public RPC
have unknown receipt ordering; retain ties instead of fabricating FIFO times.

**Evaluation:** freeze features and tip hash at each prediction; join later
confirmed transactions by hash. Split chronologically with all snapshots of a
transaction in one partition and a gap at least as long as the target horizon.
Compare fee/weight bins, age-only and combined features with Brier score,
calibration and horizon coverage on later blocks; report complete-follow-up and
censored cohorts separately. Split quiet/full-pool regimes, large/small weights,
observation coverage and source. Do not label unseen or still-pending entries as
failed predictions. A fee feature that has no variation in a cohort cannot be
claimed to improve it.

### UP4 — Eligibility and miner-backlog discrepancy audit (P2; after UP2/UP3)

**Code evidence:** `get_miner_data` returns a next-height context and backlog
([handler:1986–2019][miner-handler], [Blockchain:1753–1772][miner-core]). Its
default pool scope is public, and
[tx_pool.cpp:1038–1088][miner-backlog] sorts by fee/weight only when total weight
exceeds a target of **112.5% of the current median**, checks readiness and key
image conflicts, then stops **after** the accepted cumulative weight passes
that target. The returned weight can therefore overshoot the target by the
last accepted entry. This list is a candidate supply for mining, not the exact
transaction set of a future block and not a full pool census. A cached failed
input check is specific to the tip hash
([tx_pool.cpp:1432–1451][readiness]).

**Hypothesis:** a candidate's presence in this local miner backlog and observed
state flags may explain high-fee non-inclusion cases better than rank alone.
Per-entry RPC exposes `kept_by_block`, `relayed`, `do_not_relay`,
`double_spend_seen`, failure height/hash and max-used-block metadata
([pool fields][pool-info]); none is a complete per-poll eligibility verdict.

**Evaluation:** freeze matched pool/miner views with their separate request
times and tip hashes; discard or label cross-tip comparisons. Compare UP3 with
and without these fields using the same future confirmation cohorts. Report
full-pool denominator, returned-backlog coverage/weight, missing-field rate and
tip-specific failure markers. Investigate reorg, eviction, public/private-scope
and stale-template discrepancies before attributing them to fee behavior.
Expired entries are explicitly removed by
[tx_pool.cpp:726–740][eviction], another reason disappearance must remain an
unknown outcome until a block join resolves it. Do not interpret key-image
conflict flags as evidence of a sender or common wallet ownership.

## Acceptance boundary

These four items are **proposed**, not completed experiments. The completed work
is the pinned-source inspection and two aggregate availability probes. A first
implementation should pass UP1/UP2 collection-quality checks before advertising
UP3/UP4 forecast skill. Any resulting forecast must show its target horizon,
baseline, later-window results, retained/censored counts and observation scope.
None of these inputs belongs in deterministic ring-resolution evidence merely
because the node software exposes it.

[routes-pool]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.h#L126-L150
[routes-backlog]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.h#L176-L180
[handlers-pool]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L1650-L1742
[pool-info]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1186-L1224
[categories]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/blockchain_db/blockchain_db.cpp#L47-L75
[receive-initial]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L223-L244
[receive-upgrade]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L285-L330
[relay-schedule]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L843-L851
[relay-update]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L859-L889
[pool-stats]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1099-L1133
[raw-backlog]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1025-L1035
[backlog-type]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server_commands_defs.h#L1640-L1664
[backlog-handler]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L3322-L3338
[backlog-default]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/cryptonote_core.h#L507
[fee-order]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.h#L63-L77
[template-selection]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1558-L1632
[template-ready]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1650-L1691
[miner-handler]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L1986-L2019
[miner-core]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L1753-L1772
[miner-backlog]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1038-L1088
[miner-default]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.h#L284-L294
[readiness]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L1432-L1451
[eviction]: https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_pool.cpp#L726-L740
