---
summary: Runtime module map and scanning, analysis, scoring, and export flow.
status: maintained
reviewed: 2026-09-11
---

# Pipeline

Parent: [architecture](index.md).

| Module | Responsibility |
| --- | --- |
| [main.py](../../main.py) | Argument parsing and command orchestration |
| [monero_rpc.py](../../monero_rpc.py) | HTTP RPC calls, transaction decoding, batching, connection/timeout retries |
| [scanner.py](../../scanner.py) | Resume scans, parse key inputs, expand relative offsets, persist ring members |
| [models.py](../../models.py) | SQLite schema, reads, predictions, and resolution recording |
| [analyzer.py](../../analyzer.py) | Candidate sets, inverse output index, cascade elimination, statistics |
| [scorer.py](../../scorer.py) | Feature extraction, training, scoring, verification, soft cascade |
| [brain.py](../../brain.py) | Read-through evidence graph and resolution ancestry |
| [dashboard_export.py](../../dashboard_export.py) | Evidence-class counts and bounded prediction/inspector records |
| [docs/index.html](../../docs/index.html) | Dashboard consuming [exported data](../../docs/data.json) |

## Execution

`Monero node → RPC → Scanner → SQLite → Analyzer → Scorer / exports`

`SQLite → Brain → JSON explanations and lineage`

`Scanner.scan()` resumes after the maximum stored block height. Relative key
offsets become absolute indices through cumulative sums. Rings retain the
amount alongside each index. Each new block, its transactions, and its ring
members are written under one SQLite savepoint. Incomplete/mismatched RPC
responses or decoding/write failures roll back that entire block; a caught scan
failure commits previously completed blocks in the current batch. Retrying a stored
block skips it, preventing duplicate memberships. RPC status, HTTP, connection,
timeout, and malformed-response failures surface as `RPCError`.

Resume still uses the maximum height: it does not audit or repair legacy partial
blocks, gaps, or reorganizations, and stored heights are skipped without checking
their current chain hashes. A clean new scan receives the atomic-ingestion
guarantee; an older database needs an independent completeness audit. RPC
validation checks response structure and requested identities, not cryptographic
block/transaction validity. Synthetic failure and retry coverage lives in
[scanner tests](../../tests/test_scanner.py).

`Analyzer.run()` loads all rings and builds an inverse index. It resolves
original size-one rings before applying eliminations, imports old claims into
history, loads stored resolutions, then runs cascade passes until the work queue
is empty or the pass limit is reached. A ring reduced to zero members is reported
as a possible data inconsistency.

Existing hypothesis resolutions are also loaded. Dependency tracking preserves
their hypothesis status through later cascades. Calling plain `analyze` on a
database previously used for soft cascades does not remove those hypotheses.

The scorer trains on deterministic labels. Prediction-only scoring saves guesses;
soft cascade applies guesses to the analyzer and records their descendants.
See [evaluation](../research/evaluation.md) before interpreting accuracy.

Exports run analysis before writing JSON. `export-viz` optionally trains ML and
appends dashboard history when the scan or evidence summary changes. Read
[command effects](../workflows/development.md) before invoking reporting commands.
