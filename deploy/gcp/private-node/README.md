# Private Monero node pilot

Status: **prepared, not provisioned; deferred by user choice**. The user chose to
keep only the public-RPC pilot, so no private-node running cost has been incurred. Release verification succeeded on
2026-09-12 UTC; [the recorded proof](verification/result.json) identifies the
exact artifact, signature key, and vendored signed checksum list. The existing
collector and its RPC endpoint are unchanged. The prospective pool recorder can
start independently using public RPC, with a separate source cohort for a future
private node. See [the research design](../../../brain/research/private-node-observations.md).

## Recommended resources and cost

Use a separate `tracegrove-node` VM in project
`tracegrove-research-20260911`, zone `us-central1-a`, existing VPC/subnet
`tracegrove`. Recommended: Debian 12, `e2-standard-2` (2 vCPU / 8 GiB), 20 GiB
balanced boot disk, **300 GiB balanced data disk**, pruning enabled. The collector
already has an 8 GiB VM and a 6 GiB analyzer memory cap; co-location would compete
for memory and storage. A separate node preserves the existing workload.

Monero's [official systemd guide](https://docs.getmonero.org/running-node/monerod-systemd/)
recommends at least 4 GiB RAM and 250 GiB available SSD space for pruning,
625 GiB for full storage. Its chain-size estimates—100 GiB pruned / 250 GiB
full—are explicitly dated **2026-01-20**, not measurements of today's chain.
The proposed disk provides additional headroom; pilot measurements must track
actual growth. Full storage would use a 750 GiB data disk in the alternative below.

Incremental USD estimates checked 2026-09-12, on-demand Iowa pricing, 730 hours:

| Component | Rate | Pruned monthly estimate |
| --- | --- | ---: |
| `e2-standard-2` | $0.06701142/hour | $48.92 |
| 320 GiB total `pd-balanced` | $0.000136986/GiB-hour | $32.00 |
| One ephemeral external IPv4 | $0.005/hour | $3.65 |
| Fixed subtotal | Excludes outbound traffic | **$84.57** |

Sources: [VM pricing](https://cloud.google.com/products/compute/pricing/general-purpose),
[disk pricing](https://cloud.google.com/compute/disks-image-pricing),
[network/IP pricing](https://cloud.google.com/vpc/network-pricing).
Seven days with all resources provisioned costs about **$19.46 fixed**.
The 750 GiB full-storage alternative is approximately **$129.57/month fixed**.
These estimates omit tax, snapshots, paid monitoring, and any negotiated discounts.

The node uses **Standard Tier** external IPv4 for outbound P2P, avoiding a separate
Cloud NAT deployment. Upload/download limits are 64 / 8,192 KiB/s and the pilot
accepts no incoming P2P connections. Continuous upload at the configured limit
would be approximately 160.4 GiB per 730 hours. Standard Tier currently includes
200 GiB/month shared across the billing account, then $0.085/GiB in the first paid
tier. Without assuming any free allowance, that payload estimate is **$13.63**.
The daemon limiter is **not a hard billing cap**: protocol overhead, other VM
traffic, bursts, and rate changes can add usage. Inbound traffic and same-zone
internal-IP traffic are uncharged under the cited network rates. Record actual
egress during the pilot rather than assuming the free allowance remains available.

No automatic expiry is installed. Stopping the VM releases its ephemeral public
IP, but the retained 320 GiB of disks continues to cost approximately $32/month.
Deleting a VM does not delete its data disk; deletion protection is enabled.
Review cost approval before applying the prepared plan.

## RPC and validation contract

The internal node address is reserved as `10.78.0.3`; the observed collector
address is `10.78.0.2`. The provisioner discovers and validates the collector
address and generates these rules for the node's dedicated tag:

- Priority 800: SSH port 22 only from IAP's `35.235.240.0/20`.
- Priority 800: RPC port 18081 only from the collector's exact internal `/32`.
- Priority 900: deny all remaining incoming traffic, including public RPC/P2P.

The daemon binds unrestricted RPC to its private IPv4 so node-local pool receipt
times remain available to the trusted recorder. It has no wallet keys. This is a
trusted-client boundary: the allowed collector can call administrative RPCs.
The VM has no service account, public HTTP server, unrestricted public RPC, or
public P2P rule. Standard external IPv4 supplies outbound connectivity only under
these firewall rules. SSH uses IAP; no persistent tunnel is needed for RPC.

The config disables daemon bootstrap, pruned-block downloading, compiled fast
block synchronization, and DNS checkpoint retrieval. Pruning occurs while
retaining the transaction base. Ordinary release consensus rules/checkpoints
still apply; an independently operated node is not independent software.
`db-sync-mode=safe:sync` favors durable writes. These choices can make initial sync
slow on 2 vCPU; **no sync completion time is established**. Measure height/hour,
memory, free disk, and tip agreement before deciding to retain or resize it.
The service caps memory at 6 GiB and CPU at 190%; review those caps and
`max-concurrency` when changing machine size. RandomX needs executable memory,
so the service deliberately does not enable `MemoryDenyWriteExecute`.

Pruned compatibility is supported by the pinned source, not yet a deployed-node
measurement. At source commit `4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5`:

- [`get_transactions`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L1097-L1120)
  decodes a pruned transaction base when its prunable blob is absent, even if the
  caller did not request pruning.
- The [base serialization](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_basic/cryptonote_basic.h#L320-L345)
  includes the [prefix's `vin`, `vout`, and `extra`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/cryptonote_basic/cryptonote_basic.h#L170-L190).
  These are sufficient for the current scanner's key images, amounts, offsets,
  and output counts. Missing historical signatures/range proofs cannot support
  a later experiment requiring complete original transaction blobs.
- [`get_block`](https://github.com/monero-project/monero/blob/4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5/src/rpc/core_rpc_server.cpp#L2642-L2715)
  returns the block JSON and transaction hashes. The pool recorder also uses
  `get_info`, `get_block_header_by_height`, and `/get_transaction_pool`.

Before using this node as a new observation source, require mainnet,
`synchronized=true`, `busy_syncing=false`, `restricted=false`, `untrusted=false`,
known version, and no bootstrap source. Check a small bounded sample of old
`get_block` + `get_transactions` responses, exact `(amount,index)` output joins,
and a recent tip against the current source. Receipt times describe this node's
observations. Do not relabel an existing public-RPC observer database as private;
start a new cohort. **Do not switch the historical collector automatically.**

## Prepare, install, verify

Planning is read-only by default and prints the exact proposed commands:

```bash
python3 deploy/gcp/private-node/provision.py --project tracegrove-research-20260911
python3 -m unittest discover -s deploy/gcp/private-node -p test_packaging.py -v
bash -n deploy/gcp/private-node/install.sh deploy/gcp/private-node/deploy.sh
```

After reviewing the added cost and authorizing resource creation, append
`--apply` to the provision command. Existing matching resources are reused;
conflicting firewalls, VM tags/service accounts, or disks cause a failure.
The provisioner never resizes, deletes, or formats a disk.

Commit the prepared package first. Then the recommended default-node deployment is:

```bash
bash deploy/gcp/private-node/deploy.sh tracegrove-research-20260911 FULL_SOURCE_COMMIT --initialize-empty-disk
```

This wrapper targets the recommended node name, zone, address and pruned mode.
For custom provisioning options, adjust the wrapper's explicit values or run
`install.sh PACKAGE_DIR FULL_SOURCE_COMMIT MODE PRIVATE_IPV4` through IAP.
The initializer accepts only the separately attached `google-monero-data` disk.
Formatting requires the explicit flag, no partition/signature/mount, minimum
size, and an empty mount directory. Existing ext4 pilot state is reused without
formatting; it must carry label `MONERO_DATA`. The mounted disk is checked by
device identity. Data lives under `/var/lib/monero`; missing mounts prevent service
startup. A matching active release is left running; a different active release or
configuration requires an explicit maintenance window and fails closed.

The installer verifies the signed checksum list in an isolated GPG keyring,
requires binaryFate fingerprint
`81AC591FE9C4B65C5806AFC3F0AF4D462A0BDF92`, checks the exact archive filename and
SHA256, and extracts only the regular `monerod` file. It records archive/binary
hashes and source `REVISION` under `/opt/monero/releases/COMMIT`. Vendoring the
signed list avoids following a later mutable release list. See the
[official verification instructions](https://docs.getmonero.org/interacting/verify-monero-binaries/)
and [v0.18.5.1 release](https://github.com/monero-project/monero/releases/tag/v0.18.5.1).

After installation, inspect with IAP:

```bash
gcloud compute ssh tracegrove-node --project tracegrove-research-20260911 --zone us-central1-a --tunnel-through-iap --command 'sudo systemctl status monerod --no-pager; sudo journalctl -u monerod --no-pager -n 30; df -h /var/lib/monero; sudo ss -lntp'
```

Use the private address from the collector VM for the synchronization and RPC
checks above. Confirm the public IPv4 cannot reach ports 18080/18081 and only IAP
can reach SSH. Record results before enabling a private-source recorder cohort.
