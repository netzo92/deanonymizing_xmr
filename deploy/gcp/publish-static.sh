#!/usr/bin/env bash
# Publish a committed dashboard and knowledge snapshot without restarting collection.
set -euo pipefail
project=''
zone=us-central1-a
name=xmr-research
commit=''
prepare_only=''
usage() {
  cat <<'EOF'
Usage: deploy/gcp/publish-static.sh --commit COMMIT --project PROJECT [options]
  --zone ZONE          default: us-central1-a
  --name NAME          default: xmr-research
  --prepare-only DIR   build reviewable artifacts locally; no Google Cloud calls

Rebuilds the Markdown brain from the committed source bundle, then publishes only
the explicit UI asset allowlist. The managed nginx configuration may be reloaded.
Runtime code, collector service, SQLite, data.json, collector-status.json,
release.json, and private configuration are preserved.
EOF
}
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) project=${2:?}; shift 2 ;;
    --zone) zone=${2:?}; shift 2 ;;
    --name) name=${2:?}; shift 2 ;;
    --commit) commit=${2:?}; shift 2 ;;
    --prepare-only) prepare_only=${2:?}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n $commit && ( -n $project || -n $prepare_only ) ]] || { usage >&2; exit 2; }
for value in "$zone" "$name" ${project:+"$project"}; do
  [[ $value =~ ^[a-z][a-z0-9-]*$ ]] || { echo 'Invalid Google Cloud resource name.' >&2; exit 2; }
done
repo=$(git rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "${commit}^{commit}")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || { echo 'Unable to resolve source revision.' >&2; exit 1; }
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
mkdir "$temporary/source" "$temporary/payload"

# The complete, committed source context is needed to validate links in brain/.
# It stays local; neither the source archive nor its data snapshot is uploaded.
git -C "$repo" archive --format=tar.gz --output "$temporary/source.tar.gz" "$revision" -- \
  main.py collector.py live_observer.py pool_observer.py analyzer.py models.py scorer.py scanner.py monero_rpc.py brain.py \
  dashboard_export.py protocol_eras.py brain_export.py task_activity.py requirements.txt README.md AGENTS.md brain tests research \
  autoresearch_results.md autoresearch-results.tsv references/README.md references/monero-source.json \
  docs/index.html docs/dashboard.js docs/dashboard.css docs/data.json docs/brain.json \
  docs/brain-view.js docs/brain-view.css docs/research-analytics.js docs/research-analytics.css \
  docs/evidence-graph.js docs/evidence-graph.css \
  docs/feature-audit.js docs/feature-audit.css docs/feature-audit.json docs/progress-view.js docs/progress-view.css docs/live-feed.js docs/live-feed.css docs/todos.html docs/todo-results.js docs/todo-results.css docs/research-progress.json docs/eras.html docs/era-view.js docs/era-view.css docs/protocol-eras.json docs/pool.html docs/pool-view.js docs/pool-view.css docs/task-activity-view.js docs/task-activity-view.css docs/task-activity.json docs/hypotheses.json deploy/gcp
tar -xzf "$temporary/source.tar.gz" -C "$temporary/source"
printf '%s\n' "$revision" > "$temporary/source/REVISION"
python3 "$temporary/source/brain_export.py" --root "$temporary/source" \
  --output "$temporary/source/docs/brain.json"
python3 "$temporary/source/task_activity.py" --root "$temporary/source" \
  --output "$temporary/source/docs/task-activity.json" --validate-only
cmp "$temporary/source/research/hypotheses.json" "$temporary/source/docs/hypotheses.json" || {
  echo 'Public hypothesis ledger differs from its canonical source.' >&2; exit 1;
}

python3 - "$temporary" "$revision" <<'PY'
import hashlib, json, pathlib, sys, tarfile
from datetime import datetime, timezone

work = pathlib.Path(sys.argv[1])
source = work / 'source'
activity = json.loads((source / 'docs/task-activity.json').read_text())
if activity.get('working_tree_changes') or activity.get('summary', {}).get('pending'):
    raise SystemExit('Commit source changes and rebuild task history before publishing.')
assets = (
    'index.html', 'dashboard.js', 'dashboard.css', 'brain.json',
    'brain-view.js', 'brain-view.css', 'research-analytics.js', 'research-analytics.css',
    'evidence-graph.js', 'evidence-graph.css',
    'feature-audit.js', 'feature-audit.css', 'feature-audit.json',
    'progress-view.js', 'progress-view.css', 'live-feed.js', 'live-feed.css', 'todos.html', 'todo-results.js', 'todo-results.css', 'research-progress.json',
    'eras.html', 'era-view.js', 'era-view.css', 'protocol-eras.json',
    'pool.html', 'pool-view.js', 'pool-view.css',
    'task-activity-view.js', 'task-activity-view.css', 'task-activity.json', 'hypotheses.json',
)
hashes = {}
for asset in assets:
    path = source / 'docs' / asset
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f'Committed public asset must be a regular file: {asset}')
    content = path.read_bytes()
    (work / 'payload' / asset).write_bytes(content)
    hashes[asset] = hashlib.sha256(content).hexdigest()
nginx = source / 'deploy/gcp/nginx.conf'
installer = source / 'deploy/gcp/install-static.sh'
if nginx.is_symlink() or installer.is_symlink() or not installer.is_file():
    raise SystemExit('Committed static installer and nginx configuration are required.')
(work / 'payload/nginx.conf').write_bytes(nginx.read_bytes())
(work / 'install-static.sh').write_bytes(installer.read_bytes())
brain = json.loads((work / 'payload/brain.json').read_text())
if brain.get('source_commit') != sys.argv[2]:
    raise SystemExit('Rebuilt brain does not match the requested revision.')
manifest = {
    'schema_version': 1,
    'source_commit': sys.argv[2],
    'prepared_at': datetime.now(timezone.utc).isoformat(),
    'assets': hashes,
    'source_bundle_sha256': hashlib.sha256((work / 'source.tar.gz').read_bytes()).hexdigest(),
    'brain_content_sha256': brain['content_sha256'],
    'nginx_config_sha256': hashlib.sha256(nginx.read_bytes()).hexdigest(),
}
(work / 'payload/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
with tarfile.open(work / 'static.tar.gz', 'w:gz') as archive:
    for path in sorted((work / 'payload').iterdir()):
        archive.add(path, arcname=path.name, recursive=False)
print(json.dumps(manifest, indent=2))
PY

if [[ -n $prepare_only ]]; then
  mkdir -p "$prepare_only"
  for artifact in static.tar.gz install-static.sh manifest.json; do
    [[ ! -e $prepare_only/$artifact ]] || { echo "Refusing to replace $prepare_only/$artifact" >&2; exit 1; }
  done
  cp "$temporary/static.tar.gz" "$temporary/install-static.sh" "$prepare_only/"
  cp "$temporary/payload/manifest.json" "$prepare_only/manifest.json"
  echo "Prepared committed UI $revision in $prepare_only; no remote changes."
  exit 0
fi

remote_dir=/tmp/xmr-static-$revision-$(date +%s)-$$
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "umask 077 && mkdir '$remote_dir'"
gcloud compute scp --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  "$temporary/static.tar.gz" "$temporary/install-static.sh" "$name:$remote_dir/"
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "sudo bash '$remote_dir/install-static.sh' '$remote_dir/static.tar.gz' '$revision'; result=\$?; rm -rf '$remote_dir'; exit \$result"
address=$(gcloud compute instances describe "$name" --project "$project" --zone "$zone" --format='value(networkInterfaces[0].accessConfigs[0].natIP)')
echo "Published UI $revision: http://$address/ (provenance: /ui-release.json)"
