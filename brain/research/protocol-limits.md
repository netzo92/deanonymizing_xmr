---
summary: Which historical Monero findings apply to a dataset, and which claims need new evidence.
status: maintained
reviewed: 2026-09-11
---

# Protocol eras and limits

Parent: [research](index.md). Related: [evaluation](evaluation.md),
[measurable experiments](heuristic-experiments.md), [groupings](groupings.md).

Sources for this repository: [scorer](../../scorer.py),
[scanner](../../scanner.py), [stored evidence](../../models.py).
Literature reviewed on 2026-09-11; this page records external findings and
proposed checks, not new TraceGrove benchmark results.

## Read historical claims in their original period

| Evidence | What it supports | Limit for TraceGrove |
| --- | --- | --- |
| Möser et al., 2017/2018 | Historical chain-reaction elimination and age-distribution weaknesses | Does not establish accuracy on later wallet/protocol populations |
| RingCT deployment during 2017 | Confidential amounts changed the transaction population | Visible legacy amount buckets cannot be treated as modern transfer amounts |
| August 2022 upgrade | Ring size increased from 11 to 16 | Verify observed ring sizes and protocol versions for each later dataset |
| Hammad and Victor, 2024 | Wallet-bug and specialized-output heuristics varied over 2019–2023 | Applicability windows and evaluation-label origins matter |
| OSPEAD project report, April 2025 | A statistical approach to estimating real-spend age distributions and studying decoy mismatch | The report explicitly described research without formal peer review; its estimates are not local ground truth |

The first paper reported approximately 62% exposure to chain-reaction analysis
among historical inputs with decoys, and an approximately 80% newest-member
guessing estimate in its studied population. These are the paper's historical
measurements, not expected performance for current transactions or this model.
See [Möser et al.](https://arxiv.org/abs/1704.04299).

RingCT began in January 2017 and became mandatory in September 2017; the Monero
project explains its amount-hiding role in [RingCT](https://web.getmonero.org/resources/moneropedia/ringCT.html).
The [2022 upgrade announcement](https://beta.getmonero.org/blog/2022/04/20/network-upgrade-july-2022/)
documents the ring-size change. These dates identify boundaries to test, rather
than one timeless model population.

The [2024 paper](https://arxiv.org/html/2408.05332v1) evaluates specific wallet bugs
and output patterns over historical windows, using both ground truth and
comparisons between heuristics. Agreement between heuristics should be labeled
separately from independent validation. The [2025 OSPEAD report](https://web.getmonero.org/2025/04/05/ospead-optimal-ring-signature-research.html)
motivates measuring distribution drift; it does not justify assigning a fixed
modern success rate to TraceGrove's approximate gamma features.

## Distinguish the object and the claim

One-time output addresses are distinct from published receiving addresses.
Recovering or guessing a ring's actual spent output does not itself identify a
wallet address or person. See the official [stealth-address explanation](https://web.getmonero.org/resources/moneropedia/stealthaddress.html).

Keep four separate evidence labels in reports and visualizations: observed
transaction structure; deterministic conclusions under stated assumptions;
predictions verified against later eligible evidence; and unverified hypotheses.
Controlled-wallet labels form their own evaluation source. An edge labeled
"shared candidate" must not silently become "same owner."

For a uniform guess in a ring with `n` members, expected top-1 accuracy is `1/n`.
Average this over the evaluated rings; `1/16 = 6.25%` applies only to a cohort
whose rings all have 16 members. Candidate removal, ranking accuracy, and
ownership clustering require separate metrics and denominators.

## Prioritized TODOs

- [ ] **RL1 — Protocol cohort manifest (P0; unimplemented).** Inputs: block height/hash/version, transaction version/RingCT type, ring size, scan coverage, and label method. Baseline: today's aggregate report. Success: every evaluated ring belongs to a declared cohort, including an explicit unknown category, and cohort counts reconcile with the evaluation denominator. Failure modes: deriving wallet versions from the chain without evidence; treating export date as chain-data date; allowing missing metadata to disappear from totals.
- [ ] **RL2 — Applicability ledger (P1; research design).** Inputs: original source, heuristic assumptions, applicable heights or wallet versions, independent label source, and frozen run IDs. Baseline: one pooled heuristic/model result. Success: publish both in-window and out-of-window performance with coverage, retaining null or negative findings. Failure modes: using the same heuristic to create labels and claim validation; extending a bug-specific result beyond its documented window.
- [ ] **RL3 — Claims audit in the display (P1; unimplemented).** Inputs: exported evidence types and cohort manifest. Baseline: current aggregate resolution and prediction counts. Success: every displayed percentage identifies its cohort and denominator, and group edges retain their actual evidence meaning. Failure modes: interpreting uncalibrated scores as probabilities, treating legacy resolutions as universal proof, or presenting output groups as recovered public addresses.
