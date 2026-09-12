# GCP collector and interactive dashboard

One Debian 12 Compute Engine VM runs the Python collector under systemd and
serves its exported dashboard through nginx. This is a **research collector
using an external Monero RPC endpoint**, not a full Monero daemon. The default
VM is `e2-standard-2`, with a 100 GB balanced persistent boot disk. The collector
process can need more memory and disk as the scanned dataset grows.

Source: [collector](../../collector.py), [service](collector.service),
[nginx configuration](nginx.conf), [provisioner](provision.sh),
[release deployment](deploy.sh), [release installer](install-release.sh),
[static publication](publish-static.sh), [static installer](install-static.sh).

## First deployment

Use an existing billing-enabled project and an authenticated Google Cloud CLI.
The operator needs permission to create Compute Engine/network resources, enable
the Compute and IAP APIs, and use IAP SSH. The VM has **no attached service
account**; normal collection needs no Google API credentials. IAP requires
`roles/iap.tunnelResourceAccessor` and sufficient Compute/SSH permissions; these
scripts do not grant project IAM roles.

Run from this checkout:

```bash
# Review optional region, machine, disk and subnet overrides with --help.
bash deploy/gcp/provision.sh --project YOUR_PROJECT
```

The script creates a dedicated custom VPC and subnet, a reserved external IPv4
address, and firewall rules for public TCP 80 and TCP 22 from the IAP forwarding
range only. Matching resources are reused; an existing VM is never replaced or
resized. A mismatching existing firewall/VM fails validation. Keep this network
dedicated to the application and review any separately added ingress rules.
Deletion protection is enabled and the boot disk is retained on VM deletion.

Wait for bootstrap to finish. Its log ends with `XMR bootstrap ready`:

```bash
gcloud compute ssh xmr-research --project YOUR_PROJECT --zone us-central1-a \
  --tunnel-through-iap \
  --command 'sudo journalctl -u google-startup-scripts.service --no-pager -n 50'
```

Copy [collector.env.example](collector.env.example) to a private file outside
the checkout, set `MONERO_RPC_URL`, and leave the numeric defaults or adjust them.
`PREDICTION_EVERY=0` disables new model scoring. Use unquoted `KEY=value` entries;
URLs must contain no whitespace.
Optional `MAX_RINGS`, `MIN_FREE_GB`, `RPC_DELAY_MS`, and `CONFIRMATIONS` entries
control dataset capacity, the free-disk floor, RPC request pacing, and chain-tip
lag. If omitted, the service uses 2,000,000 rings, 5 GB free, 100 ms, and 10 blocks
respectively. The ring limit is checked before a batch, so a batch can cross it.
The RPC URL is configuration, not a browser setting. Never commit credentials
or put them in VM metadata.

Commit the source changes before deployment. Deployment requires an explicit
commit; `git archive` includes an allowlist of committed application and dashboard
files. It excludes database files and unrelated local files. Supply the existing
local database to resume its saved scan height:

```bash
bash deploy/gcp/deploy.sh --project YOUR_PROJECT \
  --commit FULL_GIT_COMMIT --env-file /tmp/xmr-collector.env \
  --seed-db monero_analysis.db
```

`--seed-db` uses SQLite's online backup API and checks the resulting backup, then
copies it through IAP. The installer validates it again and imports it **only if
the remote database does not exist**. Omit this option to begin with an empty
database. Transfers can take several minutes for a large database. No existing
database, prediction history, or current dashboard JSON is overwritten during
an upgrade. On first install only, the committed dashboard snapshot is copied
until the collector publishes its first successful export.

Open the printed `http://IP_ADDRESS/` URL. Public routes expose only the dashboard
assets, JSON exports, and health endpoint. SQLite and model artifacts remain in
`/var/lib/xmr`, outside the web root. The public site is HTTP; add a domain and
TLS before using any future authenticated UI. The current dashboard loads
Chart.js from jsDelivr, as GitHub Pages does.

## Operation and updates

The collector runs as the unprivileged `xmr` account. The default cycle scans up
to 1,000 blocks, exports fresh data, waits 300 seconds, and schedules prediction
work every six cycles. Check [collector.py](../../collector.py) for exact progress,
failure, disk-limit, and prediction behavior. systemd restarts the process if it
exits and starts it after reboot. nginx serves the latest completed export.
The service throttles memory pressure above 5 GB and has a hard 6 GB cap, leaving
room for nginx and the OS on the default 8 GB VM. If a workload hits the cap,
systemd may kill and restart it; inspect its logs before increasing capacity.
When resizing the VM, review `MemoryHigh` and `MemoryMax` with
`sudo systemctl edit xmr-collector.service` alongside the ring limit. Changing
the machine type alone does not change the service's memory caps.

```bash
gcloud compute ssh xmr-research --project YOUR_PROJECT --zone us-central1-a \
  --tunnel-through-iap \
  --command 'sudo systemctl status xmr-collector.service --no-pager; sudo journalctl -u xmr-collector.service --no-pager -n 80; df -h /var/lib/xmr'
```

- `/healthz` checks nginx availability; it does not establish collector freshness.
- `/collector-status.json` reports collector progress and errors.
- `/data.json` contains the dashboard's exported evidence and timestamps.
- `/release.json` records the exact deployed commit and deployment UTC time.
- `/ui-release.json` records the separately published UI commit, asset hashes,
  knowledge hash, publication time, and observed runtime revision/PID.

For an update, commit and push the new code, then run `deploy.sh` with the new
commit and the same environment file. Omit `--seed-db`. Releasing code stops the
collector briefly, installs dependencies, switches `/opt/xmr/current`, then
restarts the service. Source releases remain in `/opt/xmr/releases/COMMIT`;
installed dependency versions are recorded in `dependency-versions.txt` there.
The release also contains a plain `REVISION` file for model source provenance
when running without a `.git` directory.
Python dependencies follow the repository's version ranges, so a source commit
alone does not pin an identical dependency environment. Both code and dependency
changes should be validated before deployment.

### Publish dashboard and research notes while collection continues

Use the static publisher for committed UI, knowledge, and aggregate feature-audit
updates that do not require changing collector behavior:

```bash
# Optional: inspect the exact payload and SHA-256 manifest locally first.
bash deploy/gcp/publish-static.sh --commit FULL_GIT_COMMIT \
  --prepare-only /tmp/tracegrove-static-review

bash deploy/gcp/publish-static.sh --project YOUR_PROJECT \
  --name xmr-research --zone us-central1-a --commit FULL_GIT_COMMIT
```

Preparation archives the committed source allowlist and rebuilds `brain.json`
with that revision, validating its repository links before any cloud command.
Uncommitted changes are excluded. Only the 32 public UI/knowledge/audit assets,
their checksum manifest, and the committed nginx template are transferred with
the pinned static installer. The source context used for link validation remains
local; `data.json` and private collector state are not in the upload payload.

The installer shares the full-deployment lock, checks runtime provenance, and
records the collector PID before and after publication. It preserves
`/opt/xmr/current`, runtime `REVISION`, `release.json`, dependencies, SQLite,
`data.json`, `collector-status.json`, `live-observations.json`, and the environment file. It issues no
collector stop/start/restart commands. The PID observation is a publication
check, not a guarantee against later systemd restarts.

When new public assets require routes, the installer tests and reloads the
committed nginx configuration without restarting collection. It refuses to
overwrite unrecognized nginx changes, such as an operator's uncommitted TLS or
hostname configuration; incorporate those settings into the reviewed committed
configuration first. An existing managed nginx site and runtime deployment are
required, so use `deploy.sh` for initial provisioning or collector changes.

Files are replaced atomically one at a time, with `index.html` last and
`ui-release.json` published after checks pass. This is **not an atomic update of
the whole asset set**: requests can briefly observe mixed UI versions during
publication. Detected failures restore replaced assets and nginx configuration;
host crashes or forced termination can still require rerunning publication.
Runtime provenance remains in `/release.json`; use `/ui-release.json` for UI
provenance after static updates. The collector continues publishing its own
data and status snapshots independently.

Local guard, rollback, payload, and committed-source tests:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_static_publication.py' -v
bash -n deploy/gcp/publish-static.sh deploy/gcp/install-static.sh
```

To inspect or pause collection, use IAP SSH and `sudo systemctl stop
xmr-collector.service`; `sudo systemctl start xmr-collector.service` resumes it.
Stopping only the collector leaves the last completed dashboard online. Use
`sudo systemctl disable --now xmr-collector.service` to keep it stopped after a
reboot. Do not run another writer against the live SQLite database.

Take an SQLite online backup or stop the collector before making a disk snapshot
that needs database consistency. This starter deployment does not schedule
backups, TLS issuance, or alerts. Manage these once the desired retention,
hostname, and notification destination are known.

## Cost and capacity

Billable components are the running VM, persistent disk, reserved external IPv4,
internet egress, and any snapshots or monitoring added later. Stopping a VM stops
most compute charges but does not delete its disk or release its reserved IP.
Deletion protection and disk retention intentionally require explicit cleanup.
See current [Compute Engine pricing](https://cloud.google.com/compute/all-pricing)
and [VPC network pricing](https://cloud.google.com/vpc/network-pricing) for the
chosen region and usage; this repository does not assume a fixed monthly bill.

The 100 GB disk is an initial collector allocation, not capacity for a full Monero
node or indefinitely growing research data. Check disk and RAM during catch-up;
set a collector height limit or pause collection before expanding resource use.

Official setup references: [IAP SSH](https://docs.cloud.google.com/compute/docs/connect/ssh-using-iap),
[IAP access requirements](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding),
[instance creation flags](https://docs.cloud.google.com/sdk/gcloud/reference/compute/instances/create),
and [IAP file transfer](https://docs.cloud.google.com/sdk/gcloud/reference/compute/scp).


## Separate current-chain observer

Full releases also install `xmr-live-observer.service` and pin its code through
`/opt/xmr/observer-current`. The observer uses `/var/lib/xmr/live_observations.db`
and atomically writes `/var/www/xmr/live-observations.json`; it does not read or
write the historical analysis database. Optional environment settings are
`LIVE_OBSERVER_INTERVAL_SECONDS=120`, `LIVE_OBSERVER_BLOCKS_PER_CYCLE=12`, and
`LIVE_OBSERVER_CONFIRMATIONS=10`. Defaults sample the latest 12 confirmed blocks
initially, retain 1,440 summaries, and export 120; request, response and cycle
budgets prevent unbounded fetches. Skipped ranges, missing ring counts and source
provenance are explicit in JSON. A tail-hash mismatch requires operator review.

Inspect with `sudo systemctl status xmr-live-observer.service` and
`sudo journalctl -u xmr-live-observer.service --no-pager -n 30`. Stop/restart this
service independently of `xmr-collector.service`. Static UI publication preserves
both services and their mutable exports. A copied observer JSON in `docs/` is a
GitHub Pages snapshot and is never installed over the running observer's export.
The browser checks both data feeds every 60 seconds; collection/export duration
and RPC availability determine when new measurements actually arrive.

## Prospective pool observations

The bounded pool recorder can run on the existing collector VM. It has a separate
immutable runtime, `/opt/xmr/pool-current`, a dedicated private database at
`/var/lib/xmr-pool/pool_observations.db`, and its own systemd resource limits.
It publishes only aggregate `pool-observations.json` for [pool.html](../../docs/pool.html).
`pool-release.json` records the deployed observer commit and payload hashes.
Neither installing this service nor publishing its page restarts the existing
two collectors. The pool installer checks their PIDs before and after deployment.

Copy [pool-observer.env.example](pool-observer.env.example) to a private file,
then deploy committed code and UI separately:

```bash
bash deploy/gcp/deploy-pool.sh --project YOUR_PROJECT --name tracegrove \
  --commit FULL_GIT_COMMIT --env-file /tmp/tracegrove-pool.env
bash deploy/gcp/publish-static.sh --project YOUR_PROJECT --name tracegrove \
  --commit FULL_GIT_COMMIT
```

`deploy-pool.sh --prepare-only DIR` validates and prepares only code locally;
it does not copy the environment into the review artifacts. Pool deployments
share the standard deployment lock, preserve existing pool data and exports,
and restore the old pool runtime/configuration after a detected start failure.
Publication waits for a successful fresh observation from the new runtime; the
service being active alone is insufficient.
Full collector releases do not upgrade this separately pinned pool service.
Check a successful observation after every upgrade: an active process alone
does not establish a working RPC or fresh data.

Default limits: one cycle then a 60-second wait, 24 requests/45 seconds per
cycle, 8 MiB per response, 1,000 pool entries per response, six followed blocks
per cycle, two-block confirmation lag, 20,000 tracked transactions, 200,000
sighting rows, 24-hour raw retention, six-hour unresolved follow-up, 250 MiB free
disk floor and 256 MiB SQLite cap. The service has a 384 MiB memory cap and 25%
CPU quota. A limit/error retains explicit coverage gaps; it is not a zero-pool
observation. The pilot is not a permanent study archive.

Only complete accepted pool lists update current counts. Transactions become
confirmed through observed block membership; disappearance alone stays absent.
Published delay includes local polling and the confirmation lag. Initial inventory,
cross-session and interrupted follow-up records are excluded from the delay
sample. PN2 controlled coverage and PN3 forecast evaluation remain open in the
[research plan](../../brain/research/private-node-observations.md).

### Private validating node

The [private-node package](private-node/README.md) prepares a separate VM and
verified Monero release. The recommended pruned configuration would cost approximately
$85/month before egress. The user chose to keep only the public-RPC pilot; this
extra VM and its disks have not been created.
It starts with a 300 GiB retained SSD data disk and 20 GiB boot disk. RPC is
reachable only from the collector's internal IP; SSH uses IAP. No public RPC or
inbound P2P service is configured. The existing historical collector retains its
current source until node synchronization and required RPC checks succeed.

Changing the pool source or visibility requires a new cohort database. The
recorder intentionally refuses to mix public and private sources in the same
existing store. Review the service database path and preserve the previous cohort
before any cutover; merely changing `POOL_NODE_URL` will fail safely.


## Task activity and hypothesis outcomes

The TODO page includes `task-activity.json` (dated first-parent Git history) and
`hypotheses.json` (research outcomes, with evidence and scope). Both are static
research publications. New chain data does not change them or close tasks.
The page checks for published updates every 60 seconds and highlights unread
activity locally in the current browser. No notification provider or extra VM
is installed.

Follow the [task-reporting workflow](../../brain/workflows/task-reporting.md)
after changing checklists or conclusions. Generate task history in a full local
Git checkout after committing source changes, then commit the generated snapshot.
A deployment archive has no `.git`; it validates the saved history against the
committed Markdown and requires the public hypothesis JSON to match its canonical
research source. Stale exports fail before publication. GitHub Pages receives the
committed snapshots, while the GCP static publisher preserves all running
collectors and their independently changing exports.
