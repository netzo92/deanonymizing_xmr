---
summary: Ten source-pinned mainnet eras separate protocol changes, theoretical implications, measured coverage and remaining experiments.
status: maintained
reviewed: 2026-09-11
---

# Security and privacy eras

Parent: [research](index.md). Related: [protocol limits](protocol-limits.md),
[upstream source audit](upstream-heuristics.md), [evaluation](evaluation.md).

The canonical [era manifest](../../docs/protocol-eras.json) groups the sixteen
mainnet hardfork versions in the [pinned Monero release](../../references/monero-source.json)
into ten readable periods. It separates protocol changes, their theoretical
implications, claim limits, and links to existing experiments. It does not assign
a numerical security score or a tracing-success rate to an era.

## Boundaries and taxonomy

All ranges are **inclusive**. Heights come from
[`hardforks.cpp` at commit 4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/hardforks/hardforks.cpp#L34-L78).
The upstream version-one entry starts at height 1; the manifest explicitly assigns
genesis height 0 to version one so the taxonomy has no gap. The open final range
means the latest fork represented by this source pin; future protocol changes
require reviewing and updating the manifest.

| Era | Mainnet heights | Fork versions | Selected changes |
| --- | --- | --- | --- |
| Visible amounts and optional decoys | 0–1,009,826 | 1 | Visible amount buckets; ordinary minimum-ring requirement not yet active |
| Minimum mixing and amount structure | 1,009,827–1,220,515 | 2–3 | Ordinary minimum of three members; legacy amount-form checks |
| RingCT introduction | 1,220,516–1,399,999 | 4–5 | Confidential transfer amounts become available |
| RingCT and five-member rings | 1,400,000–1,545,999 | 6 | Ordinary RingCT enforcement and larger minimum rings |
| Seven-member minimum | 1,546,000–1,685,554 | 7 | Larger minimum rings and sorted key-image inputs |
| Bulletproofs and eleven-member rings | 1,685,555–1,787,999 | 8–9 | New range-proof format; ordinary rings of eleven |
| Compact RingCT encoding | 1,788,000–1,978,432 | 10–11 | `RCTTypeBulletproof2` format transition |
| Uniformity and minimum output age | 1,978,433–2,209,999 | 12 | Equal ring sizes per transaction, minimum non-miner v2 output count, minimum referenced-output age |
| CLSAG signatures | 2,210,000–2,688,887 | 13–14 | Signature-format transition and deterministic timestamp unlocking |
| Sixteen-member rings, Bulletproofs+ and view tags | 2,688,888 onward | 15–16 | Larger ordinary rings, more compact proofs and faster wallet scanning |

Every individual fork remains in the manifest, including versions grouped in the
same era. Human-readable periods come from official project history and upgrade
descriptions. **They are not computed from the timestamp field in the hardfork
table**, which can contain announcement or scheduling metadata. Heights determine
classification, not the approximate month or year label.

This is a selected privacy/format/integrity taxonomy, not an exhaustive history
of consensus, network security, proof-of-work, cryptographic patches or wallet
bugs. Wallet changes and vulnerability windows may cross these boundaries.

## Preserve the transition rules

RingCT hides transfer amounts and changes the observable amount population; its
2017 introduction and later ordinary enforcement should not be conflated.
Legacy unmixable-amount exceptions remain explicit in the pinned validator.
See [Moneropedia: RingCT](https://web.getmonero.org/resources/moneropedia/ringCT.html)
and [input/version validation](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3261-L3354).

HF8 first permits Bulletproofs; HF9 rejects Borromean range proofs. HF10 then
permits the compact `RCTTypeBulletproof2` format and HF11 rejects the earlier
Bulletproof type. `Bulletproof2` is not Bulletproofs+. These are format and
efficiency changes, not local measurements of anonymity.
See the [2018 release explanation](https://web.getmonero.org/2018/10/11/monero-0.13.0-released.html)
and [exact proof-format conditions](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3010-L3057).

HF13 permits CLSAG. HF14 rejects older MLSAG-based RingCT types, with **two
explicit grandfathered historical transactions**. CLSAG supplies smaller, faster
signatures with signer ambiguity; the validator exceptions are not real-spend
labels. See the [official CLSAG explanation](https://web.getmonero.org/2020/07/31/clsag-audit.html)
and [transition/exception implementation](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3060-L3092).

HF15 permits an ordinary ring-size transition from eleven to sixteen, accepts
both proof families, and allows tagged or untagged outputs of a consistent type
within one transaction. HF16 completes enforcement of ordinary sixteen-member
rings, Bulletproofs+ and tagged outputs. Follow executable version conditions,
not a shortened comment suggesting view tags were immediately mandatory at HF15.
View tags help wallets scan outputs; they are not public owner identifiers.
See the [2022 release explanation](https://www.getmonero.org/2022/07/19/monero-0.18.0.0-released.html),
[proof transition](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/blockchain.cpp#L3096-L3120),
and [view-tag conditions](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_basic/cryptonote_format_utils.cpp#L978-L1007).

## What our data supports

The original analyzed snapshot ends at height 58,900 in 2014; the historical
collector is still catching up within version one. Its stored resolutions,
feature audits and baseline comparisons apply to the sampled legacy population.
Singleton resolutions, propagated exclusions, verified predictions and unresolved
rings retain separate denominators. They cannot measure the effect of upgrades
that the analyzed data has not reached. See the
[feature audit](feature-variation-audit.md) and [historical baselines](heuristic-baselines.md).

The separate [current observer](../workflows/cloud.md) samples recent confirmed
blocks. Its recorded ring-size distributions establish current activity and
structure in the latest source-defined period. They contain no real-spend labels
and do not establish current-chain prediction accuracy. Middle eras with no
collected coverage must say **no observed coverage**, rather than zero success
or zero weakness.

Classification currently means **mainnet height inferred**. The scanner has not
preserved block major version, transaction version, RingCT type or the full
metadata needed for validation against every fork rule. Height-based counts are
therefore a useful first layer, not completion of the full RL1 protocol-context
plan. Missing height/network context remains unknown. Original ring size and
current surviving candidates must not be mixed.

Larger rings increase nominal candidate counts, but uniform `1/n` guessing is
only a baseline for a specified ring population. Proof efficiency, confidentiality,
selection quality, false merges, and verified ranking accuracy are distinct
outcomes. Consensus eligibility constrains all candidates and does not identify
the actual spender. No pooled literature percentage is assigned to these eras.

## Maintenance and remaining work

- [x] **ER1 — Source-pinned era taxonomy.** Ten eras and all sixteen exact fork boundaries are recorded with genesis normalization, source links, transition exceptions, theory/claim separation, and links to experiments. [Manifest tests](../../tests/test_protocol_eras.py) check a pinned activation fixture and reference metadata without requiring the ignored clone in CI; when the clone exists, they also verify source hashes, table entries and line ranges. This completes the taxonomy layer, not full validation of observed transactions or the public-page deployment.
- [ ] **ER2 — Populate comparable evidence by era.** Collect missing periods and preserve actual block/transaction versions, RingCT types, output origins, and label eligibility. Report coverage and missing metadata first; only then run the existing HE2/HE3 and UA3/UA4 experiments on declared populations. Success includes an honest negative result. No upgrade-specific accuracy gain is assumed.
- [ ] **ER3 — Sample matched upgrade windows.** A separate bounded collector could sample declared windows before, during and after each fork without waiting for the full historical backfill. Freeze block hashes and compare ring-size distributions, proof formats, output ages and missingness against the pinned rules. Publish window sizes, transition exclusions and source coverage. This can test structural expectations quickly; a discontinuity in these statistics cannot establish an upgrade's causal effect on anonymity or prediction accuracy.

An additional display idea is a second timeline for wallet-specific behavior and
documented vulnerability windows. Those boundaries need not match consensus
forks. Keep its implementation under the existing [RL2 applicability ledger](protocol-limits.md)
plan, and require original sources and eligible labels before adding any success
rates. A private node would improve reproducibility and data access; it would not
automatically supply known real-spend or ownership labels.

The manifest is the canonical boundary source for display and aggregation. When
changing the source pin, review each rule and official description, update the
independent test fixture intentionally, and regenerate the public note graph.
Keep measurements in their dated artifacts rather than copying changing counts
into this taxonomy.
