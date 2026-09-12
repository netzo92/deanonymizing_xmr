# Pinned Monero wallet decoy-selection audit

Reviewed **2026-09-11**. This is a source comparison and an offline formula
diagnostic, not a new prediction experiment. No production scorer, source
database, wallet, or cloud resource was changed.

Upstream: `monero-project/monero`, release **v0.18.5.1**, commit
**`4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5`**, checked out cleanly under
`references/monero`. Every upstream link below pins that commit. The inspected
local [scorer](../scorer.py) has SHA-256
`1505277b4a79879fdcc7d1dc42e37075911716f7fa66ffd8eacd1389b78e998b`;
TraceGrove HEAD was `dd5452bb07f212f6c099e6d159fab1a48874fa2e`.
Upstream `src/wallet/wallet2.cpp` SHA-256 was
`d6a77caa126ee0b55c15be13b29ed554e8faf552435ce9186267389537f96fa6`.

Related measured evidence: [40-output origin audit](../brain/research/output-origin-audit.md),
[EA1 feature audit](../brain/research/feature-variation-audit.md),
[historical baseline experiment](../brain/research/heuristic-baselines.md),
and [evaluation boundaries](../brain/research/evaluation.md).

## 1. The recent-window formula is a mixture, not a flat age region

The wallet draws `Z ~ Gamma(19.28, 1/1.61)` and sets `X = exp(Z)`. With
`U = 1,200 seconds` and `W = 1,800 seconds`, it sets `Y = X − U` when `X > U`;
otherwise it draws an integer uniformly from `0` through `W − 1`.
This is a displacement from the unlocked output boundary, before converting
seconds to output indices. The constants and branch are in
[wallet2.cpp:143–154](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L143-L154)
and [1056–1088](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L1056-L1088).

For a **continuous approximation** to that integer redraw, the pre-index
proposal density is, for `y >= 0`:

```text
g(y) = f_X(y + U) + [F_X(U) / W] × indicator(0 <= y < W)
f_X(t) = gamma_pdf(log(t); shape=19.28, scale=1/1.61) / t
```

The exact redraw instead puts mass `F_X(U)/W` at each eligible integer second;
it coexists with the shifted continuous component. Neither expression yet
describes the final probability of selecting an output.

In local `scorer.py:100–139`, every estimated age at most `W` receives `1/W`.
It omits the branch probability `F_X(U)` and the shifted-gamma contribution
within that interval. Above `W`, the unbounded formula equals the shifted
continuous component **only if its input is already displacement from the
unlocked boundary**. The amount-zero proxy at lines 108–111 estimates distance
from a synthetic chain tip instead, so its reference point also needs review.
Replacing one formula while preserving that incompatible age proxy is incomplete.

**Computed formula diagnostic:** using SciPy **1.17.1**, `F_X(1200)` is
`0.020783935343739724`, and `F_X(3000)` is `0.057242243456902014`.
Ignoring the scorer's numerical clipping, its piecewise expression would
integrate to `1 + P(X > U + W) = 1.942757756543098` over nonnegative
post-unlock displacement. It is therefore not a normalized proposal density.
This calculation does **not** measure a prediction error rate, actual age
error, or improvement; tree features need not themselves be normalized densities.
It specifically contradicts interpreting this feature as the wallet's density.

Reproduce the arithmetic without chain data:

```bash
venv/bin/python - <<'PY'
import math
from scipy.stats import gamma
F = lambda seconds: gamma.cdf(math.log(seconds), a=19.28, scale=1/1.61)
print(F(1200), F(3000), 2 - F(3000))
PY
```

## 2. Output density, block mapping, and eligibility are part of the picker

The wallet obtains the amount-zero output distribution and constructs
cumulative counts in
[wallet2.cpp:4338–4378](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L4338-L4378).
It estimates seconds per output from at most the last **262,800 distribution
entries**, corresponding to 365 days at the 120-second target. It derives the
selectable cumulative prefix using the default spendable age. For an array of
length `N` and spendable age 10, the exclusive end is `N−9` and the last
selectable cumulative count is at `N−10`.
[wallet2.cpp:1039–1051](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L1039-L1051)
provides these exact indexing conventions.

After truncating `Y / average_output_time` to an integer output displacement,
the picker rejects an out-of-range displacement, counts back from the selectable
prefix, uses `lower_bound` on cumulative counts, then samples uniformly among
the outputs in that chosen block. Empty selected blocks produce a bad-pick
sentinel. Reproduction should preserve the pinned code's exact boundary
behavior, rather than silently replacing `lower_bound` with a different search.
[wallet2.cpp:1088–1102](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L1088-L1102).

Local `scorer.py:78–98` instead uses the maximum **referenced** output index
divided by maximum input height, and whole-snapshot maxima for legacy buckets.
Those are neither an output-creation census nor an output rate at prediction
time. Later references can change features of old inputs. Separately,
`distance_from_tx` at line 243 subtracts an output index from a block height,
mixing unrelated units. The hard-coded rank target `0.75` at lines 218–224 is
not a quantity derived by this wallet picker.

Eligibility is also more than subtracting ten blocks. The wallet oversamples
to cover unusable outputs, adds extra requests for the longer coinbase lock,
and rejects locked/duplicate/invalid-key candidates before accepting the final
ring. See [9372–9414](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9372-L9414)
and [9047–9073](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9047-L9073).
An RPC `unlocked` value obtained today cannot establish eligibility at an old
spend height; historical checks need origin height, transaction lock metadata,
coinbase status, and the rules at that cutoff.

## 3. The nonzero-amount path is not just global triangular progress

For nonzero amounts the pinned wallet requests unlocked-output histograms and
recent counts using a **1.8-day** cutoff. The default recent quota is **50%** of
the applicable request count, with integer rounding, bounds, and a decrement
when the real output lies in the recent region. It uses triangular draws within
recent or full ranges, with additional conditional pre/post-fork paths.
See [constants:118–121](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L118-L121),
[histograms:9274–9296](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9274-L9296),
[quota:9489–9505](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9489-L9505),
and [selection:9626–9668](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9626-L9668).
The segregation-height default is 99,999,999, with a mainnet override; merely
finding a conditional branch does not establish that it ran for a transaction.
[Defaults:136–139](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L136-L139),
[override:15895–15906](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L15895-L15906).

Local `scorer.py:141–152` returns identical triangular and progress columns
`(index+1)/(maximum_referenced_index+1)`. For the simplified continuous-uniform
draw `floor(N*sqrt(U))`, the discrete mass is `(2i+1)/N²`; the local progress
feature is still monotonic in `i`, so merely normalizing it would not change
its within-bucket ordering. The upstream finite 53-bit draw and subsequent
rejection/quotas require further accounting for an exact simulator.

Crucially, **v0.18.5.1's retained legacy branch does not identify the wallet
algorithm used in 2014**. All rows in our frozen EA1 source are nonzero amounts
at heights 0–58,900. Mainnet v2 begins at 1,009,827, and the configured v1 target
was 60 seconds, versus 120 in v2.
[Hardfork schedule:34–39](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/hardforks/hardforks.cpp#L34-L39),
[targets:80–81](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_config.h#L80-L81).
Neither target is a measurement of elapsed wall-clock time. Applying modern
gamma assumptions or multiplying historical block ages by 120 cannot validate
a historical elapsed-time feature.

## 4. Final rings are not independent draws from one age density

The wallet samples without replacement and retains its private picking order
while sorting RPC requests. It seeds the real output, reuses saved rings where
available, prefers those saved members, and saves the constructed rings for
reuse. It also rejects blackballed outputs initially but can relax that filter
if too few usable outputs remain.
[Picking order:9380–9398](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9380-L9398),
[saved-ring and fallback logic:9513–9597](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9513-L9597),
[final assembly and storage:9794–9887](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9794-L9887).

For the RCT path, the wrapper can retry construction up to three times after
a transaction sanity failure. The sanity function checks transaction-wide
unique-index count and median index when its size/available-output guards
permit. These are wallet/relay checks in the inspected path, not a universal
claim about consensus or all wallets.
[Wallet retries:9200–9224](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9200-L9224),
[sanity function:76–101](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_core/tx_sanity_check.cpp#L76-L101).
The separate light-wallet path requests server-provided random outputs, adding
another reason not to assign this local gamma generator to every chain ring.
[Light-wallet path:9076–9099](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/wallet/wallet2.cpp#L9076-L9099).

Overlap can therefore arise through decoy construction and reuse mechanisms.
It is not evidence of common address/wallet ownership. Graph baselines should
use exact `(amount,index)` identities, declared time cutoffs, and tie-aware
statistics. They should not silently promote a frequently reused candidate
or a shared transaction into a deterministic ownership link.

## Five actionable experiments

All five are **proposed, not implemented**. A source mismatch establishes an
opportunity to test, not a prediction improvement.

| Priority / ID | Inputs and change to test | Baseline and success measure | Failure modes and evidence boundary |
| --- | --- | --- | --- |
| P0 / WD1 — Proposal kernel | Fixed synthetic cumulative-output arrays; reproduce gamma redraw, unlock-prefix indexing, integer displacement, block mapping, and invalid-pick rejection in a separate versioned reference kernel. | Compare with the pinned C++ picker and current approximation. Report accepted/rejected draws, probability-mass normalization, per-output/binned total variation, and Monte Carlo uncertainty across declared seeds. Test zero-output blocks, exact cumulative boundaries, and just-unlocked windows. | An analytic age PDF alone is not the final output PMF. Distributional agreement is not byte-identical RNG replay or better real-spend prediction. |
| P0 / WD2 — Origin and density inputs | Cache immutable cumulative distributions, exact `(amount,index)` origins, timestamps, coinbase/lock metadata, and scan-tip hashes. Compute measured block age separately from wallet proposal displacement; make absent joins explicit. | Compare actual-age rank and the reference output PMF against current index proxies on a frozen cohort. Report join coverage, impossible-height/eligibility conflicts, feature variation, and sensitivity to output-density changes. Require historical feature values to remain unchanged when future data is appended. | Today's unlocked flag and full-snapshot referenced maxima leak or misrepresent old state. Block targets are not elapsed timestamps; reject or expose timestamp anomalies. |
| P1 / WD3 — Era-aware legacy proposal | First audit matching historical wallet revisions. For supported scenarios, supply amount-bucket unlocked/recent counts at the cutoff, ring size, known policy settings, and each hypothesized real-member context. | Compare current monotonic progress, uniform, newest, and a quota-aware triangular simulator. Measure generated quota/index distributions before any held-out ring agreement; report ring-size/era sample counts. | The pinned release's legacy path cannot be assumed for 2014. Quotas depend on the real output; conditioning must be repeated for each hypothesis rather than leaking the stored answer into a feature. |
| P1 / WD4 — Construction-aware overlap null | Simulate whole transactions with a declared real-output age baseline, no-replacement sampling, final acceptance, optional saved-ring reuse, and explicit blackball scenarios. | Compare simple independent-decoy draws and minimum-reuse ranking. Measure expected shared-output counts, all/partial reuse ties, transaction-level unique counts, and deviations on supported held-out cohorts. | Private wallet state is generally unknown. Show scenario sensitivity; neither a low null probability nor a connected component establishes ownership. |
| P0 / WD5 — Versioned paired ablation | Preserve `ring-member-v1`; create new feature versions for supported corrections. Use frozen original-ring contexts and grouped temporal evaluation, deriving constant/duplicate masks from training folds only. Test tied-rank handling and the ungrounded rank-0.75 feature separately. | Unchanged scorer plus uniform/newest/oldest/minimum-reuse rules on exactly the same eligible test rings. Report top-1 agreement, precision at matched coverage, paired changes across seeds, and negative results; evaluate calibration only with appropriate outcome labels. | Fewer duplicate columns need not improve a forest. Present retrospective labels are selective and have unknown historical availability; no modern or forward gain follows from the existing baseline. |

The upstream [output-selection tests:112–220](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/tests/unit_tests/output_selection.cpp#L112-L220)
provide existing median-age, output-density, and distribution checks useful
for WD1. They were inspected, **not compiled or executed** in this audit.

EA1 already measured four globally constant columns and four exact duplicate
pairs in its bounded legacy sample. The origin audit found valid creation
heights for 40 selected outputs with ages 15–41,150 blocks, while all three
gamma features stayed constant. The full historical baseline found 82.31%
expected minimum-reuse agreement and 78.45% newest agreement on 12,076
already-resolved multi-member rings. These are useful regression baselines;
they supply **no modern amount-zero validation** of WD1–WD4.

Finally, `P(output chosen as a decoy | wallet policy, chain state)` is not
`P(output is the real spend | observed ring)`. The latter needs a model of
real-output selection, candidate hypotheses, the complete ring-construction
likelihood, and relevant priors. Normalizing decoy likelihoods across ring
members—or normalizing their reciprocals—does not establish calibrated
real-spend probabilities under these dependent, incompletely observed policies.
