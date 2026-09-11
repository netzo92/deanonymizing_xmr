# GCP collector and interactive dashboard

One Debian 12 Compute Engine VM runs the Python collector under systemd and
serves its exported dashboard through nginx. This is a **research collector
using an external Monero RPC endpoint**, not a full Monero daemon. The default
VM is `e2-standard-2`, with a 100 GB balanced persistent boot disk. The collector
process can need more memory and disk as the scanned dataset grows.

Source: [collector](../../collector.py), [service](collector.service),
[nginx configuration](nginx.conf), [provisioner](provision.sh),
[release deployment](deploy.sh), [release installer](install-release.sh).

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
