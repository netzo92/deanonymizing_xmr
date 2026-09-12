# Monero node metadata and validation audit

Reviewed 2026-09-12 UTC (2026-09-11 America/Los_Angeles). This is a read-only
source audit and a proposed measurement plan. No node was built, no new RPC
requests were made for this audit, no database was opened, and no scoring or
deployment behavior changed.

The strongest next steps are exact origin-age joins and output-population
counts frozen at the prediction cutoff. The node also supplies useful integrity
checks and explicit transaction co-creation links. None of its public output
metadata identifies which ring member was actually spent.

## Source and scope

Upstream is the official [`monero-project/monero`](https://github.com/monero-project/monero)
repository, tag **v0.18.5.1**, commit
**`4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5`**, checked out at
`references/monero`. The tracked checkout was clean. All upstream links below
pin that commit and exact source lines; this is not a claim about every older
wallet or node release.

TraceGrove source HEAD during review was
`dd5452bb07f212f6c099e6d159fab1a48874fa2e`. Relevant local source hashes:

| File | SHA-256 |
| --- | --- |
| `scorer.py` | `1505277b4a79879fdcc7d1dc42e37075911716f7fa66ffd8eacd1389b78e998b` |
| `scanner.py` | `38f655daabf4ca6f928c6150185926a7b0e71a6a811fa554eb582f8714f8ee98` |
| `monero_rpc.py` | `1024d84d207fe6ff02ea28dba36234ba624f627df04aa690de0ac22c825dcda6` |
| `models.py` | `44e57b1d8d852bf45a799d8afab81d59f694e82039399663aee4ac71d481c2e9` |

The original research snapshot ends at height 58,900 in 2014 and contains no
amount-zero membership rows. Its earlier [40-output origin audit](../brain/research/output-origin-audit.md)
retrieved origin heights and transaction IDs for all sampled identities, with
selected-context ages from 15 to 41,150 blocks. That measured feasibility, not
model improvement. The separate [current-chain observer](../live_observer.py)
publishes recent block activity; it does not supply modern real-spend labels.

## NA1 — Exact origin-age features and join integrity

**What source proves.** `/get_outs` accepts `(amount,index)` requests and returns
public key, commitment mask, current unlock flag, creation height, and optional
creating transaction ID. The blockchain implementation loads output metadata
and fills transaction IDs by the same request identities
([`blockchain.cpp:2279–2314`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L2279-L2314)).
LMDB processes amounts/offsets in request order, with distinct legacy and
amount-zero storage branches
([`db_lmdb.cpp:4159–4205`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/blockchain_db/lmdb/db_lmdb.cpp#L4159-L4205)).
The response schema does not echo amount/index, so the client must preserve and
validate the positional association
([`core_rpc_server_commands_defs.h:570–608`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server_commands_defs.h#L570-L608)).

**Current gap and proposed measurement.** [The scanner](../scanner.py) stores
referencing inputs and output counts, not output origins. The generic
[`get_outs`](../monero_rpc.py) helper hardcodes amount zero and omits `get_txid`;
it must not be reused unchanged for the 2014 amount buckets. The separate
origin-audit client already uses actual identities. Extend that bounded audit
into a versioned origin cache, then compare `input_height - origin_height`,
within-ring age rank, and explicit missing-origin flags against current index
proxies. First report join coverage, conflicting mappings, future-origin
violations, age distributions, and within-ring variation. Accuracy ablation
comes only after frozen temporal labels/splits exist; no gain is assumed.

**Era and failure limits.** Creation-height joins apply to both legacy and
modern outputs. Keep ages in blocks. The scorer hardcodes 120 seconds per block;
upstream defines 60 seconds for v1 and 120 for v2
([`cryptonote_config.h:80–89`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_config.h#L80-L89)).
Neither target is a measured elapsed time. If block timestamps are joined,
label differences as source-reported block-time differences and retain negative
or conflicting values for audit. Reject partial batches, wrong-network mappings,
reorg conflicts, and ambiguous output contexts; never fill missing age with zero.

## NA2 — Output-population counts at a frozen cutoff

**What source proves.** `get_output_distribution` accepts amount, height bounds,
and cumulative/noncumulative encoding. Amount-zero distributions start no earlier
than v4 and use cumulative RingCT counts; nonzero amounts use their own output
buckets
([`blockchain.cpp:2334–2372`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L2334-L2372)).
`to_height=0` means the current tip. Restricted RPC permits only the single
amount-zero distribution, explicitly rejecting legacy amounts
([`core_rpc_server.cpp:3342–3376`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L3342-L3376)).
Noncumulative conversion and the `base` term are explicit
([`rpc_handler.cpp:14–24`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/rpc_handler.cpp#L14-L24)).

**Current gap and proposed measurement.** The scorer derives amount maxima and
average output density from outputs referenced anywhere in the loaded snapshot
([`scorer.py`](../scorer.py), `_amount_max_indices` and `_outputs_per_block`).
These are neither complete output populations nor necessarily available at a
historical prediction cutoff. Cache distributions by network, amount, explicit
cutoff height/hash, and source revision. Measure the difference between current
proxy denominators and actual created-output counts; replay a fixed input at
later scan heights and require its cutoff-based values to stay unchanged.
Compare direct origin-height joins with distribution-derived origins where both
are available. Start with a small declared set of legacy denominations and one
modern amount-zero cohort.

**Era and failure limits.** Use an explicit cutoff before the containing block
for a historical pre-inclusion experiment; define separately which maturity and
wallet-selection filters the experiment applies. Counts of created outputs are
not counts of selectable decoys, and neither is a calibrated selection
probability. A private node enables legacy distribution queries that a restricted
public node rejects, but does not validate a triangular or gamma wallet model.
Do not issue one whole-chain request per ring: the nonzero LMDB path allocates
`db_height - from_height` entries even for a bounded `to_height`
([`db_lmdb.cpp:4343–4382`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/blockchain_db/lmdb/db_lmdb.cpp#L4343-L4382)).
Bound denomination count, response size, time, and cache retention; test encoding,
start-height/base semantics, and reorg invalidation before deriving features.

## NA3 — Historical unlock and minimum-age integrity audit

**What source proves.** `get_outs.unlocked` uses the node's current hardfork
version and `is_tx_spendtime_unlocked`; it is not evaluated at the old referencing
input height
([`blockchain.cpp:2304–2306`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L2304-L2306)).
Unlock values can be block heights or timestamps. Timestamp evaluation uses wall
clock before HF13 and adjusted chain time from HF13
([`blockchain.cpp:3840–3863`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3840-L3863),
[`cryptonote_config.h:185–191`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_config.h#L185-L191)).
Every referenced output is passed through the unlock check
([`blockchain.cpp:216–251`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L216-L251),
[`blockchain.cpp:3886–3919`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3886-L3919)).
HF12 adds a separate minimum-age check using the newest referenced output's
height; **the RPC unlock flag alone does not include this age check**
([`blockchain.cpp:3455–3459`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3455-L3459)).

**Proposed measurement.** Fetch raw `unlock_time` from the creating transaction,
origin height, and coinbase status into the origin cache. Replay applicable
height-based constraints at a documented historical boundary, with boundary
fixtures for the allowed one-block tolerance, 10-block age rule, and coinbase
unlock. Coinbase transactions must set unlock time to creation height plus 60
([`blockchain.cpp:1300–1316`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L1300-L1316),
[`cryptonote_config.h:40–49`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_config.h#L40-L49)).
Report eligible, conflicting, missing, and historically indeterminate candidate
counts. Unexpected conflicts trigger data/source review, not elimination.

**Era and failure limits.** Do not retroactively apply HF12's consensus age rule
to the 2014 dataset. Pre-HF13 timestamp eligibility cannot generally be replayed
exactly from block timestamps alone because historical validation wall time is
not preserved there. On a valid accepted ring these checks constrain all
candidates; passing does not favor the real spender. They are integrity audits,
not deterministic labels or a promise of useful within-ring discrimination.

## NA4 — Protocol cohorts and legacy-exception audit

**What source proves.** Mainnet v1 extends through height 1,009,826; v4 starts
at 1,220,516; HF12 at 1,978,433; HF15/16 at 2,688,888/2,689,608
([`hardforks.cpp:34–78`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/hardforks/hardforks.cpp#L34-L78)).
Ring-size restrictions start at HF2, evolve by version, and include unmixable
nonzero-amount exceptions. HF15 permits 11 or 16 during the transition; ordinary
mixable HF16 rings require 16. HF12 also requires equal ring sizes among a
transaction's inputs
([`blockchain.cpp:3261–3354`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3261-L3354)).

**Current gap and proposed measurement.** The scanner discards block major
version, transaction version and raw unlock metadata. Persist those fields in a
future sidecar/scan format and label protocol cohorts explicitly. Audit observed
ring-size/amount/version combinations against the appropriate rules, including
exceptions and per-transaction context. Plot original and surviving ring sizes
separately. Freeze the local data cutoff and report cohort counts, conflicts,
unknowns, and singleton prevalence before any pooled model comparison.

**Era and failure limits.** The original and current catch-up research data are
2014/v1, so modern ring-size requirements do not explain their singleton labels.
Recent observer activity is a different population with no new resolution labels.
Consensus versions also do not uniquely identify wallet version or decoy picker.
Never use the fork schedule alone to assign a wallet-selection likelihood.

## NA5 — Origin-transaction groups and dependence-aware evaluation

**What source proves.** Each requested output can map to its creating transaction
ID; the DB separately distinguishes transaction-local output index from an
amount-bucket index
([`blockchain.cpp:2308–2314`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L2308-L2314),
[`blockchain_db.h:103–104`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/blockchain_db/blockchain_db.h#L103-L104)).
Confirmed `/get_transactions` results expose creation block metadata and output
indices, providing a second join path
([`core_rpc_server.cpp:1177–1200`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L1177-L1200)).

**Proposed measurement and hypothesis.** Add a typed `created_by_transaction`
edge without changing resolution edges. Measure distinct origin transactions
per ring, repeated origins among candidates, origin-height concentration, and
shared-origin overlap across training/holdout rings. Compare ordinary ring splits
with time-aware splits that prevent shared-origin groups crossing the boundary;
report group sizes and excluded coverage. A later ablation could test whether
these descriptors add information at matched coverage, with frozen cutoff graph
state and declared labels. Their predictive usefulness is unmeasured.

**Era and failure limits.** Co-creation joins apply to legacy and modern outputs,
subject to source coverage. A transaction may create outputs for different
recipients, so co-creation is not common ownership, an address cluster, or evidence
that two candidates were real spends. Keep `(amount,index)` identities and local
output positions distinct; modern outputs may require version-aware handling.
Do not infer coinbase status from txid alone or infer a transaction's real output
from which origins happened to resolve later. Origin-group train/test separation
can reduce one dependency without proving all samples independent.

## Provenance and experiment boundary

For any implementation, save exact requests/responses, identity order, endpoint
origin, fetch time, network, cutoff height/hash, node/source version, join status,
and response hashes in a separate bounded cache. Record `untrusted` and bootstrap
state explicitly. Upstream defaults `untrusted=false` and sets it true when
forwarding through a bootstrap daemon
([`core_rpc_server_commands_defs.h:101–110`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server_commands_defs.h#L101-L110),
[`core_rpc_server.cpp:2414–2442`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L2414-L2442)).
That flag is server-reported provenance, not authentication or independent chain
verification. Compare a bounded sample against a fully synchronized private
validating node with bootstrap disabled; report disagreements without silently
overwriting older evidence. Multiple matching public endpoints can share an
upstream and do not establish independence.

Suggested order: NA1 origin joins, NA3/NA4 integrity and era checks, NA2 frozen
population audit, then NA5 graph descriptors and evaluation controls. Reuse the
existing [EA2 origin-age experiment](../brain/research/output-origin-audit.md)
and [evaluation requirements](../brain/research/evaluation.md). Version new
features; retain old artifacts and negative results. This report establishes
source capabilities and measurement opportunities, not prediction improvements
or modern-chain deanonymization.
