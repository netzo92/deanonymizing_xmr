---
summary: Feasibility and research TODOs for output and transaction groupings.
status: maintained
reviewed: 2026-09-11
---

# Predicted groupings

Parent: [research](index.md). Related: [predictions](predictions.md),
[dashboard TODOs](../tasks/dashboard.md),
[storage and evidence](../architecture/storage-and-evidence.md).

## What could be grouped?

Monero uses one-time output addresses; public chain data does not directly map
these outputs to published receiving addresses. See the Monero project's
[stealth-address explanation](https://web.getmonero.org/resources/moneropedia/stealthaddress.html).

The current [schema](../../models.py) stores key images, transactions, and
`(amount, index)` references. It lacks wallet-address ownership and complete
output-origin tables. A feasible initial display is an explorer of output and
transaction relationships with explicit evidence types.

| Group | Meaning | Does not establish |
| --- | --- | --- |
| Inputs in one transaction | Observed transaction structure | Which ring members were spent |
| Rings sharing candidates | Observed ring overlap | Common sender or ownership |
| Predicted co-spent outputs | Selected candidates for distinct inputs of one transaction | Correct selection of every candidate or common beneficial ownership |
| Similar transaction patterns | Statistical similarity under chosen features | A unique wallet, address, or person |
| Candidate output cluster | Hypothesized relationships supported by typed edges | An identified entity or recovered receiving address |

Outputs can appear in multiple rings as possible signers; overlap is therefore
not ownership evidence. See the official
[ring-signature explanation](https://web.getmonero.org/resources/moneropedia/ringsignatures.html).
The product distinctions above are proposed interpretations for this repository.

## Research TODOs

- [ ] **G1 — Ring-overlap explorer (P2).** The [progress view](../../docs/progress-view.js) now displays same-transaction groups, exact shared-output groups among exported rings, and bounded reported overlap pairs. These are direct relationships, not transitive ownership clusters. Full size/degree-normalized comparison and controlled validation remain open. Start with `Brain.related_rings()`, preserving amount in output identity. Compare raw overlap with size/degree-normalized similarity and inspect heavily reused candidates. Done when results are labeled ring-overlap communities, graph limits are explicit, and overlap alone never produces ownership claims.

- [ ] **G2 — Co-spend hypotheses (P2; depends on P1–P3).** Join input rings by transaction and attach deterministic/predicted candidates with provenance. Test joint candidate-selection accuracy on controlled multi-input transactions. Authorization of actual spent inputs does not by itself prove one controller or beneficial owner. Retain competing candidates and abstentions; multiplying correlated input scores does not establish joint confidence. Done when joint correctness and failure cases are measured separately from per-ring accuracy.

- [ ] **G3 — Cluster validation (P3; depends on G2).** Use synthetic graphs and locally controlled wallet transactions with known output ownership/spends. Measure pairwise precision/recall, false merges, and stability across thresholds, periods, and added blocks. Include unrelated wallets using similar construction patterns as negative controls. Compare conservative baselines before graph models. Done when labels and confidence have measured meaning on held-out data; disclose limits of generalizing controlled data to the public chain.

- [ ] **G4 — Reversible cluster records (P3; depends on G3).** Store run-specific groups, members, typed evidence edges, supporting prediction/event IDs, and superseded links. Preserve versions as evidence changes. A weak bridge must not irreversibly merge groups; connected-component transitivity is not proof of common ownership. Done when each grouping can be explained, split, and compared across runs without changing deterministic labels.

- [ ] **G5 — Output origins and controlled labels (P2).** Assess scanner/RPC changes to map `(amount, index)` to creating transaction, output position, and height, including mining outputs. Verify completeness before drawing transaction-flow edges. Keep controlled wallet output/spend labels separate from model inputs. Done when source-output joins are validated and label-derived features cannot leak answers into training.

Suggested display: select transaction → inspect input rings → toggle predicted
spend edges → inspect candidate groups and their supporting links. Keep “wallet
group” labels experimental until controlled ownership evaluation supports them.
Public-address recovery and identity attribution are not established outcomes.
