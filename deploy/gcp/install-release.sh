#!/bin/bash
# Invoked over IAP by deploy.sh. Root installs a trusted, commit-pinned archive.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# -ge 3 && $# -le 4 ]] || { echo 'Usage: install-release.sh ARCHIVE FULL_COMMIT ENV_FILE [SEED_DB]' >&2; exit 1; }
archive=$1
revision=$2
env_file=$3
seed_db=${4:-}
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full 40-character git commit.' >&2; exit 1; }
[[ -f $archive && -f $env_file ]] || { echo 'Archive or environment file missing.' >&2; exit 1; }
[[ -f /var/lib/xmr-bootstrap-complete && -x /opt/xmr/venv/bin/python ]] || {
  echo 'Bootstrap is incomplete. Check journalctl -u google-startup-scripts.service.' >&2; exit 1;
}
exec 9>/run/lock/xmr-deploy.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }

release=/opt/xmr/releases/$revision
staging=$(mktemp -d /opt/xmr/releases/.staging.XXXXXXXX)
trap 'rm -rf "$staging"' EXIT
tar --extract --gzip --file "$archive" --directory "$staging" --no-same-owner
printf '%s\n' "$revision" > "$staging/REVISION"
for required in collector.py requirements.txt docs/index.html docs/dashboard.js docs/dashboard.css deploy/gcp/collector.service deploy/gcp/nginx.conf; do
  [[ -f $staging/$required ]] || { echo "Release is missing $required" >&2; exit 1; }
done
python3 - "$env_file" <<'PY'
import pathlib, re, sys
values = {}
for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if not line.strip() or line.lstrip().startswith('#'):
        continue
    if not re.fullmatch(r'[A-Z_]+=[^\s\"\x27\\]+', line):
        raise SystemExit('Environment file requires unquoted KEY=value entries without whitespace.')
    key, value = line.split('=', 1)
    values[key] = value
required = {'MONERO_RPC_URL', 'BLOCKS_PER_CYCLE', 'INTERVAL_SECONDS', 'PREDICTION_EVERY'}
optional = {'MAX_RINGS', 'MIN_FREE_GB', 'RPC_DELAY_MS', 'CONFIRMATIONS'}
if not required <= values.keys() or not values.keys() <= required | optional:
    raise SystemExit('Environment file needs: ' + ', '.join(sorted(required)) +
                     '; optional: ' + ', '.join(sorted(optional)))
if not values['MONERO_RPC_URL'].startswith(('https://', 'http://')):
    raise SystemExit('MONERO_RPC_URL must start with http:// or https://')
for key in values.keys() - {'MONERO_RPC_URL', 'MIN_FREE_GB'}:
    minimum = 0 if key in {'PREDICTION_EVERY', 'RPC_DELAY_MS', 'CONFIRMATIONS'} else 1
    if not values[key].isdigit() or int(values[key]) < minimum:
        raise SystemExit(key + ' must be an integer >= ' + str(minimum))
if 'MIN_FREE_GB' in values:
    try:
        valid = 0 < float(values['MIN_FREE_GB']) < float('inf')
    except ValueError:
        valid = False
    if not valid:
        raise SystemExit('MIN_FREE_GB must be a positive finite number')
PY

# Stop the writer before changing its shared environment or seeding a new DB.
# Existing state is preserved even if the upgrade fails.
was_active=0
if systemctl is-active --quiet xmr-collector.service; then
  was_active=1
  systemctl stop xmr-collector.service
fi
trap 'if [[ $was_active == 1 ]]; then systemctl start xmr-collector.service || true; fi' ERR
/opt/xmr/venv/bin/python -m pip install --disable-pip-version-check -r "$staging/requirements.txt"
/opt/xmr/venv/bin/python "$staging/collector.py" --help >/dev/null
if [[ ! -d $release ]]; then
  chown -R root:root "$staging"
  chmod -R a+rX,go-w "$staging"
  mv "$staging" "$release"
  staging=$(mktemp -d /opt/xmr/releases/.staging.XXXXXXXX)
fi
/opt/xmr/venv/bin/python -m pip freeze > "$release/dependency-versions.txt"
printf '%s\n' "$revision" > "$release/REVISION"
if [[ -n $seed_db ]]; then
  if [[ -e /var/lib/xmr/monero_analysis.db ]]; then
    echo 'Existing remote database preserved; seed database not imported.'
  else
    python3 - "$seed_db" <<'PY'
import pathlib, sqlite3, sys
source = pathlib.Path(sys.argv[1]).resolve()
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as conn:
    if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise SystemExit('Seed database failed SQLite quick_check.')
    required = {'blocks', 'transactions', 'ring_members'}
    actual = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= actual:
        raise SystemExit('Seed database is missing required repository tables.')
PY
    install -o xmr -g xmr -m 0640 "$seed_db" /var/lib/xmr/monero_analysis.db.seed
    mv -n /var/lib/xmr/monero_analysis.db.seed /var/lib/xmr/monero_analysis.db
  fi
fi
install -o root -g xmr -m 0640 "$env_file" /etc/xmr/collector.env
ln -sfn "$release" /opt/xmr/current.next
mv -Tf /opt/xmr/current.next /opt/xmr/current
for asset in index.html dashboard.js dashboard.css; do
  install -o xmr -g xmr -m 0644 "$release/docs/$asset" "/var/www/xmr/$asset.next"
  mv -f "/var/www/xmr/$asset.next" "/var/www/xmr/$asset"
done
if [[ ! -f /var/www/xmr/data.json && -f $release/docs/data.json ]]; then
  install -o xmr -g xmr -m 0644 "$release/docs/data.json" /var/www/xmr/data.json
fi
python3 - "$revision" <<'PY'
import datetime, json, os, pathlib
target = pathlib.Path('/var/www/xmr/release.json')
temporary = target.with_suffix('.json.next')
temporary.write_text(json.dumps({'source_commit': __import__('sys').argv[1],
    'deployed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}, indent=2) + '\n')
temporary.chmod(0o644)
os.replace(temporary, target)
PY
install -m 0644 "$release/deploy/gcp/collector.service" /etc/systemd/system/xmr-collector.service
install -m 0644 "$release/deploy/gcp/nginx.conf" /etc/nginx/sites-available/xmr
ln -sfn /etc/nginx/sites-available/xmr /etc/nginx/sites-enabled/xmr
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now nginx xmr-collector.service
systemctl reload nginx
systemctl --no-pager --full status xmr-collector.service
echo "Deployed source revision $revision. Database preserved at /var/lib/xmr/monero_analysis.db."
