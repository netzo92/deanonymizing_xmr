---
summary: How to deploy and operate the persistent GCP collector and interactive site.
status: maintained
reviewed: 2026-09-11
---

# Cloud collection and hosting

Parent: [workflows](index.md).

The [GCP deployment guide](../../deploy/gcp/README.md) and scripts provision a
dedicated Compute Engine VM, then deploy a specific committed source archive.
The VM runs [collector.py](../../collector.py) through systemd and serves the
dashboard with nginx. It queries an external Monero RPC endpoint; it does not
store or validate a full Monero blockchain locally.

## State and publication

- SQLite and model artifacts live in `/var/lib/xmr`, outside the web root.
- The public site lives in `/var/www/xmr` and contains only allowed static assets/JSON.
- Optional seeding uses SQLite's online backup API and never replaces an existing remote database.
- Releases live under `/opt/xmr/releases/COMMIT`; the `current` symlink selects deployed code. `REVISION` preserves source provenance without `.git`.
- The web server serves the previous complete export while collection or analysis is in progress. Failed exports do not replace it.

GitHub Pages serves the committed snapshot from `main`'s `docs/` directory. The
GCP site updates independently as its collector publishes new results. These are
different publication paths: the collector does not automatically push data to GitHub.

## Collector cycle

One process owns an advisory database lock. Each cycle reads the node height,
checks the stored tail hash, and scans a bounded batch with a confirmation lag.
It analyzes new data, verifies outstanding predictions, optionally trains/scores,
and publishes an atomic JSON snapshot. Prediction scheduling persists in database
metadata. The default batch is 1,000 blocks, followed by a 300-second wait; new
predictions run every six successful data-update cycles.

The collector refuses known height gaps, a mismatched tail hash, a node behind
the stored scan, insufficient disk space, and the configured ring-count limit.
Block ingestion is atomic and rejects incomplete RPC responses. These checks do
not establish cryptographic chain validity or repair legacy incomplete records.
Reorganization recovery remains manual.

`collector-status.json` reports progress/error state; `release.json` identifies
deployed code. The nginx `/healthz` endpoint establishes only web availability.
Compare the last scanned block time and observed node lag with the export time;
a recently written dashboard can still contain historical chain data.

## Validation and limits

Use [collector tests](../../tests/test_collector.py) for bounded scans, restart
recovery, publication failures, and tail/gap protection; use
[scanner tests](../../tests/test_scanner.py) for incomplete RPC and transaction
rollback. Run the existing [development checks](development.md) before release.

The analyzer loads the ring graph into memory. A small VM needs monitoring and
capacity limits as history grows; it is not an unlimited whole-chain service.
Source releases record installed dependency versions, but requirements ranges
do not yet provide a reproducible lockfile. TLS, scheduled backups, and alerts
are deployment follow-ups; the initial public GCP endpoint serves HTTP.
