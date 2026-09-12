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

## Deployment record

Provisioned on 2026-09-11. The project has billing enabled, the VM is running,
and the public application and first complete collection/export cycle have been
verified. The first export completed at 2026-09-12 00:18:30 UTC.

| Resource | Configuration |
| --- | --- |
| Project | `tracegrove-research-20260911` |
| Instance | `tracegrove` |
| Zone | `us-central1-a` |
| Machine | `e2-standard-2` (2 vCPU, 8 GB RAM) |
| Persistent boot disk | 100 GB `pd-balanced`, retained on VM deletion |
| VPC and subnet | `tracegrove` |
| Public endpoint | [http://35.254.148.94/](http://35.254.148.94/) |
| First deployed source revision | `dde2fa5cdff7b895f12ab17c81440717c15ff0bb` |

Use these actual resource names when following the deployment guide's examples,
which otherwise default to `xmr-research`. Verify source provenance through
`/release.json` and `/opt/xmr/current/REVISION` for collector runtime, and
`/ui-release.json` for separately published dashboard/brain revisions and hashes.
Use the explicit `http://` address until TLS is configured.
The intended `tracegrove.io` domain is not yet registered or connected.

The first cycle advanced the seeded scan from height 58,900 to 59,900 and exported
664,239 rings. SQLite integrity, contiguous block heights, dataset identity,
collector success metadata, source revision, and reconciled evidence counts
passed validation. The tail block timestamp was 2014-05-28 05:00:27 UTC: recent
publication does not mean recent chain coverage. The live page passed desktop
and mobile Chrome interaction checks; private database/source paths returned 404.
The initial Linux release passed 67 Python tests and a synthetic real-model
training/artifact-replay smoke check. These tests do not measure real model quality.

The research-workspace release copies this first verified cloud export into the
GitHub Pages snapshot: dataset `3929c809-62e6-43ae-929b-92a9d91d4ece`, 200 included
records from 15,328 historical predictions. The local audit's original database
is a separate, unchanged source at height 58,900. Later collector cycles update
GCP independently of the committed Pages snapshot.

## State and publication

- SQLite and model artifacts live in `/var/lib/xmr`, outside the web root.
- The public site lives in `/var/www/xmr` and contains only allowed static assets/JSON.
- Optional seeding uses SQLite's online backup API and never replaces an existing remote database.
- Releases live under `/opt/xmr/releases/COMMIT`; the `current` symlink selects deployed code. `REVISION` preserves source provenance without `.git`.
- The web server serves the previous complete export while collection or analysis is in progress. Failed exports do not replace it.

GitHub Pages serves the committed snapshot from `main`'s `docs/` directory. The
GCP site updates independently as its collector publishes new results. These are
different publication paths: the collector does not automatically push data to GitHub.

For note/UI changes, [publish-static.sh](../../deploy/gcp/publish-static.sh)
rebuilds the committed brain and publishes an explicit static allowlist, leaving
the collector process, runtime release, database, and live data/status exports
untouched. It checks the collector PID before and after and can reload managed
nginx routes. [Eleven local tests](../../tests/test_static_publication.py) cover
the committed payload, provenance, guards, and rollback. The UI manifest records
asset and brain hashes separately from `/release.json`. Replacements are atomic
per file; the complete set is not one atomic transaction. See the
[deployment guide](../../deploy/gcp/README.md) for preparation and publication.

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
collector code and `ui-release.json` identifies UI code. The nginx `/healthz`
endpoint establishes only web availability.
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


## Current-chain observations

The separate [live observer](../../live_observer.py) samples recent confirmed
blocks from the configured external RPC source. It uses
`/var/lib/xmr/live_observations.db`, never the historical analysis database, and
publishes `live-observations.json` atomically. The
[service](../../deploy/gcp/live-observer.service) polls every 120 seconds with a
10-block confirmation lag, at most 12 blocks per cycle, and explicit request,
time, response-size and disk limits. It stores at most 1,440 block summaries and
exports the newest 120. Initial coverage is the latest 12 confirmed blocks;
long outages can produce explicitly recorded skipped ranges. Mismatched stored
tail hashes pause observation for review.

The page checks analysis and observation JSON every 60 seconds. Actual fresh
exports depend on RPC availability and work duration. Browser errors retain the
last successful snapshot and show stale/error state. GitHub Pages remains a
committed mirror; the GCP dashboard consumes the running services' exports.
The observer records local fetch times, source-reported block timestamps,
transaction counts, and decoded ring counts with completeness metadata. These
are current-chain activity observations, not current-chain resolved rings or
wallet ownership labels. A private validating node and prospective pool timing
collection remain separate work.

Validation: [observer tests](../../tests/test_live_observer.py),
[polling tests](../../tests/test_live_feed.js), and deployment/browser checks.
