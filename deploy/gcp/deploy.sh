#!/usr/bin/env bash
# Upload only a committed release. Run from any directory within this repository.
set -euo pipefail
project=''
zone=us-central1-a
name=xmr-research
commit=''
env_file=''
seed_db=''
usage() {
  cat <<'EOF'
Usage: deploy/gcp/deploy.sh --project PROJECT --commit COMMIT --env-file PATH [options]
  --zone ZONE     default: us-central1-a
  --name NAME     default: xmr-research
  --seed-db PATH  optional local SQLite database; backed up consistently, imported
                  only when the remote database does not exist
COMMIT is resolved to its immutable full SHA and archived from git. Uncommitted
files, SQLite databases, videos and local credentials are never included.
EOF
}
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) project=${2:?}; shift 2 ;;
    --zone) zone=${2:?}; shift 2 ;;
    --name) name=${2:?}; shift 2 ;;
    --commit) commit=${2:?}; shift 2 ;;
    --env-file) env_file=${2:?}; shift 2 ;;
    --seed-db) seed_db=${2:?}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n $project && -n $commit && -f $env_file ]] || { usage >&2; exit 2; }
for value in "$project" "$zone" "$name"; do
  [[ $value =~ ^[a-z][a-z0-9-]*$ ]] || { echo 'Invalid Google Cloud resource name.' >&2; exit 2; }
done
repo=$(git rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "${commit}^{commit}")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Unable to resolve source revision.' >&2; exit 1; }
git -C "$repo" cat-file -e "$revision:collector.py"
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
archive=$temporary/release.tar.gz
# Explicit allowlist prevents unrelated tracked files from becoming VM content.
git -C "$repo" archive --format=tar.gz --output "$archive" "$revision" -- \
  main.py collector.py live_observer.py pool_observer.py analyzer.py models.py scorer.py scanner.py monero_rpc.py brain.py \
  dashboard_export.py protocol_eras.py brain_export.py requirements.txt README.md AGENTS.md brain tests research \
  autoresearch_results.md autoresearch-results.tsv references/README.md references/monero-source.json \
  docs/index.html docs/dashboard.js docs/dashboard.css docs/data.json docs/brain.json \
  docs/brain-view.js docs/brain-view.css docs/research-analytics.js docs/research-analytics.css \
  docs/evidence-graph.js docs/evidence-graph.css docs/feature-audit.js docs/feature-audit.css \
  docs/feature-audit.json docs/progress-view.js docs/progress-view.css docs/live-feed.js docs/live-feed.css docs/todos.html docs/todo-results.js docs/todo-results.css docs/research-progress.json docs/eras.html docs/era-view.js docs/era-view.css docs/protocol-eras.json docs/pool.html docs/pool-view.js docs/pool-view.css deploy/gcp
git -C "$repo" show "$revision:deploy/gcp/install-release.sh" > "$temporary/install-release.sh"
cp "$env_file" "$temporary/collector.env"
chmod 0600 "$temporary/collector.env"
seed_argument=''
if [[ -n $seed_db ]]; then
  [[ -f $seed_db ]] || { echo 'Seed database does not exist.' >&2; exit 2; }
  python3 - "$seed_db" "$temporary/seed.db" <<'PY'
import pathlib, sqlite3, sys
source = pathlib.Path(sys.argv[1]).resolve()
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30) as reader:
    with sqlite3.connect(sys.argv[2]) as backup:
        reader.backup(backup)
        if backup.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise SystemExit('SQLite backup failed quick_check.')
pathlib.Path(sys.argv[2]).chmod(0o600)
PY
fi
remote_dir=/tmp/xmr-release-$revision-$(date +%s)-$$
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "umask 077 && mkdir '$remote_dir'"
gcloud compute scp --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  "$archive" "$temporary/install-release.sh" "$temporary/collector.env" "$name:$remote_dir/"
if [[ -n $seed_db ]]; then
  gcloud compute scp --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
    "$temporary/seed.db" "$name:$remote_dir/seed.db"
  seed_argument="'$remote_dir/seed.db'"
fi
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "sudo bash '$remote_dir/install-release.sh' '$remote_dir/release.tar.gz' '$revision' '$remote_dir/collector.env' $seed_argument; result=\$?; rm -rf '$remote_dir'; exit \$result"
address=$(gcloud compute instances describe "$name" --project "$project" --zone "$zone" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
echo "Deployed $revision: http://$address/"
echo "Collector logs: gcloud compute ssh $name --project $project --zone $zone --tunnel-through-iap --command 'sudo journalctl -u xmr-collector.service --no-pager -n 50'"
