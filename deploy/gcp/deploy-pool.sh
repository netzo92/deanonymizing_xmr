#!/usr/bin/env bash
# Deploy only the prospective observer from an immutable committed source bundle.
set -euo pipefail
project=''
name=tracegrove
zone=us-central1-a
commit=''
env_file=''
prepare_only=''
usage() {
  cat <<'EOF'
Usage: deploy/gcp/deploy-pool.sh --commit COMMIT --env-file PATH --project PROJECT [options]
  --name NAME          default: tracegrove
  --zone ZONE          default: us-central1-a
  --prepare-only DIR   build committed code locally, no cloud commands or env upload
Does not deploy UI assets or restart the historical/confirmed-block collectors.
Deploy the UI separately with publish-static.sh. Existing pool data is preserved.
EOF
}
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) project=${2:?}; shift 2 ;;
    --name) name=${2:?}; shift 2 ;;
    --zone) zone=${2:?}; shift 2 ;;
    --commit) commit=${2:?}; shift 2 ;;
    --env-file) env_file=${2:?}; shift 2 ;;
    --prepare-only) prepare_only=${2:?}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n $commit && -f $env_file && ( -n $project || -n $prepare_only ) ]] || { usage >&2; exit 2; }
for value in "$name" "$zone" ${project:+"$project"}; do
  [[ $value =~ ^[a-z][a-z0-9-]*$ ]] || { echo 'Invalid resource name.' >&2; exit 2; }
done
repo=$(git rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "${commit}^{commit}")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || exit 1
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
git -C "$repo" archive --format=tar.gz --output "$temporary/pool.tar.gz" "$revision" -- \
  pool_observer.py live_observer.py monero_rpc.py deploy/gcp/pool-observer.service
git -C "$repo" show "$revision:deploy/gcp/install-pool.py" > "$temporary/install-pool.py"
python3 - "$temporary/install-pool.py" "$env_file" "$temporary/pool.tar.gz" <<'PY'
import importlib.util,sys
spec=importlib.util.spec_from_file_location('pool_installer',sys.argv[1])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
module.environment(open(sys.argv[2]).read())
module.read_archive(sys.argv[3])
print('Committed pool payload and environment validated.')
PY
if [[ -n $prepare_only ]]; then
  mkdir -p "$prepare_only"
  for file in pool.tar.gz install-pool.py; do
    [[ ! -e $prepare_only/$file ]] || { echo 'Refusing to overwrite prepared artifact.' >&2; exit 1; }
    cp "$temporary/$file" "$prepare_only/$file"
  done
  echo "Prepared pool observer $revision. Environment was not copied."
  exit 0
fi
cp "$env_file" "$temporary/pool.env"
chmod 0600 "$temporary/pool.env"
remote_dir=/tmp/xmr-pool-$revision-$(date +%s)-$$
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet --command "umask 077 && mkdir '$remote_dir'"
gcloud compute scp --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  "$temporary/pool.tar.gz" "$temporary/install-pool.py" "$temporary/pool.env" "$name:$remote_dir/"
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "sudo python3 '$remote_dir/install-pool.py' '$remote_dir/pool.tar.gz' '$revision' '$remote_dir/pool.env'; result=\$?; rm -rf '$remote_dir'; exit \$result"
echo "Pool observer deployed from $revision; existing writer services preserved."
