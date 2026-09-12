#!/usr/bin/env bash
# Run on the separate Debian 12 node only, from a committed package.
set -euo pipefail
package=${1:?package directory required}
revision=${2:?source commit required}
mode=${3:?pruned or full required}
private_ip=${4:?private IPv4 required}
initialize=${5:-}
[[ $EUID == 0 ]] || { echo 'Run as root on the separate node VM.' >&2; exit 2; }
[[ $revision =~ ^[a-f0-9]{40}$ && ( $mode == pruned || $mode == full ) ]] || exit 2
[[ -z $initialize || $initialize == --initialize-empty-disk ]] || exit 2
[[ $(uname -m) == x86_64 ]] || { echo 'Pinned artifact is x86_64 only.' >&2; exit 2; }
source /etc/os-release
[[ $ID == debian && $VERSION_ID == 12 ]] || { echo 'This installer targets Debian 12.' >&2; exit 2; }
exec 9>/run/lock/tracegrove-node-install.lock
flock -n 9 || { echo 'Another node installation owns the lock.' >&2; exit 1; }
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
release=/opt/monero/releases/$revision
python3 - "$package/monerod.conf.in" "$temporary/monerod.conf" "$private_ip" "$mode" <<'PY'
from pathlib import Path
import sys
text=Path(sys.argv[1]).read_text().replace('@PRIVATE_IP@',sys.argv[3]).replace('@PRUNE@','1' if sys.argv[4]=='pruned' else '0')
Path(sys.argv[2]).write_text(text)
PY
device=/dev/disk/by-id/google-monero-data
[[ -b $device ]] || { echo 'Expected the separately attached monero-data disk.' >&2; exit 1; }
minimum_gib=250
[[ $mode == full ]] && minimum_gib=625
[[ $(blockdev --getsize64 "$device") -ge $((minimum_gib * 1024 * 1024 * 1024)) ]] || {
  echo 'Attached data disk is smaller than the reviewed storage recommendation.' >&2; exit 1;
}
python3 - "$private_ip" <<'PY'
import ipaddress,json,subprocess,sys
address=ipaddress.ip_address(sys.argv[1])
interfaces=json.loads(subprocess.check_output(['ip','-j','-4','addr','show']))
local={entry['local'] for interface in interfaces for entry in interface.get('addr_info',[])}
if address.version!=4 or not address.is_private or address.is_loopback or str(address) not in local:
    raise SystemExit('RPC must bind an assigned private IPv4 address.')
PY
if systemctl is-active --quiet monerod.service; then
  if [[ $(readlink -f /opt/monero/current) == "$release" ]] \
      && cmp -s "$temporary/monerod.conf" /etc/monero/monerod.conf \
      && cmp -s "$package/monerod.service" /etc/systemd/system/monerod.service; then
    python3 - "$release" <<'PY'
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]); proof=json.loads((root/'verification.json').read_text())
if hashlib.sha256((root/'monerod').read_bytes()).hexdigest()!=proof.get('monerod_sha256'):
    raise SystemExit('Installed daemon hash differs from its verification record.')
PY
    echo 'Matching node release is already active; no files or services changed.'
    exit 0
  fi
  echo 'Active node differs; refusing to stop or replace it outside an explicit maintenance window.' >&2; exit 1
fi
if [[ -e /etc/monero/monerod.conf ]] && ! cmp -s "$temporary/monerod.conf" /etc/monero/monerod.conf; then
  echo 'Existing daemon configuration differs; refusing to overwrite it.' >&2; exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg python3 util-linux e2fsprogs
archive=$temporary/monero-linux-x64-v0.18.5.1.tar.bz2
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
  --retry 3 --max-time 300 https://downloads.getmonero.org/cli/monero-linux-x64-v0.18.5.1.tar.bz2 --output "$archive"
PYTHONDONTWRITEBYTECODE=1 python3 "$package/verify_release.py" "$archive" > "$temporary/verification.json"

# Never format a partitioned, signed, mounted, or unapproved data device.
if ! mountpoint -q /var/lib/monero; then
  if [[ -d /var/lib/monero && -n $(find /var/lib/monero -mindepth 1 -maxdepth 1 -print -quit) ]]; then
    echo 'Mount directory contains state; refusing to hide it.' >&2; exit 1
  fi
  [[ $(lsblk -nr -o NAME "$device" | wc -l) -eq 1 ]] || { echo 'Data device has partitions; refusing.' >&2; exit 1; }
  [[ -z $(lsblk -nr -o MOUNTPOINT "$device" | tr -d '[:space:]') ]] || { echo 'Data device is mounted elsewhere.' >&2; exit 1; }
  filesystem=$(blkid -s TYPE -o value "$device" || true)
  if [[ -z $filesystem ]]; then
    [[ $initialize == --initialize-empty-disk ]] || { echo 'Blank disk requires explicit --initialize-empty-disk.' >&2; exit 1; }
    wipefs --no-act --json "$device" > "$temporary/signatures.json"
    python3 - "$temporary/signatures.json" <<'PY'
import json,sys
if json.load(open(sys.argv[1])).get('signatures'):
    raise SystemExit('Device contains a recognized signature; refusing to format.')
PY
    mkfs.ext4 -L MONERO_DATA "$device"
    filesystem=ext4
  fi
  [[ $filesystem == ext4 && $(blkid -s LABEL -o value "$device") == MONERO_DATA ]] || {
    echo 'Existing filesystem must be ext4 with the pilot MONERO_DATA label.' >&2; exit 1;
  }
  install -d -m 0750 /var/lib/monero
  uuid=$(blkid -s UUID -o value "$device")
  python3 - "$uuid" <<'PY'
from pathlib import Path
import sys
path=Path('/etc/fstab'); text=path.read_text(); uuid=sys.argv[1]
expected=f'UUID={uuid} /var/lib/monero ext4 defaults,nosuid,nodev,noexec 0 2'
matches=[line for line in text.splitlines() if not line.lstrip().startswith('#') and len(line.split())>1 and line.split()[1]=='/var/lib/monero']
if matches and matches != [expected]:
    raise SystemExit('Conflicting /etc/fstab mount; review before installation.')
if not matches:
    with path.open('a') as out: out.write('\n'+expected+'\n')
PY
  mount /var/lib/monero
fi
[[ $(readlink -f "$(findmnt -n -o SOURCE --target /var/lib/monero)") == $(readlink -f "$device") ]] || {
  echo 'Mounted state is not the explicitly attached node data disk.' >&2; exit 1;
}
id monero >/dev/null 2>&1 || useradd --system --home-dir /var/lib/monero --shell /usr/sbin/nologin monero
chown root:monero /var/lib/monero
chmod 0750 /var/lib/monero
install -d -o monero -g monero -m 0750 /var/lib/monero/chain /var/lib/monero/logs
install -d -o root -g monero -m 0750 /etc/monero
install -d -o root -g root -m 0755 /opt/monero/releases
python3 - "$archive" "$temporary/monerod" <<'PY'
from pathlib import Path
import shutil,sys,tarfile
with tarfile.open(sys.argv[1],'r:bz2') as archive:
    entry=archive.getmember('monero-x86_64-linux-gnu-v0.18.5.1/monerod')
    if not entry.isfile(): raise SystemExit('Unexpected binary archive member type')
    with archive.extractfile(entry) as source, open(sys.argv[2],'wb') as target:
        shutil.copyfileobj(source,target)
Path(sys.argv[2]).chmod(0o755)
PY
"$temporary/monerod" --version
python3 - "$temporary" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]); path=root/'verification.json'; proof=json.loads(path.read_text())
proof['monerod_sha256']=hashlib.sha256((root/'monerod').read_bytes()).hexdigest()
path.write_text(json.dumps(proof,indent=2)+'\n')
PY
if [[ -e $release ]]; then
  [[ $(cat "$release/REVISION") == "$revision" ]] && cmp -s "$temporary/monerod" "$release/monerod" \
    && cmp -s "$temporary/verification.json" "$release/verification.json" || {
    echo 'Existing release differs from the verified artifact; refusing to overwrite.' >&2; exit 1;
  }
else
  mkdir "$release"
  install -m 0755 "$temporary/monerod" "$release/monerod"
  install -m 0644 "$temporary/verification.json" "$release/verification.json"
  printf '%s\n' "$revision" > "$release/REVISION"
fi
install -o root -g monero -m 0640 "$temporary/monerod.conf" /etc/monero/monerod.conf
ln -sfn "$release" /opt/monero/current.next
mv -Tf /opt/monero/current.next /opt/monero/current
install -m 0644 "$package/monerod.service" /etc/systemd/system/monerod.service
systemctl daemon-reload
systemctl enable --now monerod.service
systemctl --no-pager --full status monerod.service
echo 'Node synchronization started. Do not switch collectors until synced RPC compatibility checks pass.'
