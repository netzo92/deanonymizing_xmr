#!/usr/bin/env bash
# Install only the offline archive job from a committed, bounded source bundle.
set -euo pipefail
project=''
name=tracegrove
zone=us-central1-a
commit=''
prepare_only=''
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) project=${2:?}; shift 2 ;;
    --name) name=${2:?}; shift 2 ;;
    --zone) zone=${2:?}; shift 2 ;;
    --commit) commit=${2:?}; shift 2 ;;
    --prepare-only) prepare_only=${2:?}; shift 2 ;;
    *) echo 'Usage: deploy-pool-archive.sh --commit COMMIT [--project PROJECT | --prepare-only DIR] [--name NAME] [--zone ZONE]' >&2; exit 2 ;;
  esac
done
[[ -n $commit && ( -n $project || -n $prepare_only ) ]] || exit 2
for value in "$name" "$zone" ${project:+"$project"}; do
  [[ $value =~ ^[a-z][a-z0-9-]*$ ]] || exit 2
done
repo=$(git rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "${commit}^{commit}")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || exit 2
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
git -C "$repo" archive --format=tar.gz --output "$temporary/archive.tar.gz" "$revision" -- \
  research/pool_archive.py research/run_pool_archive.py deploy/gcp/pool-archive.service deploy/gcp/pool-archive.timer
git -C "$repo" show "$revision:deploy/gcp/install-pool-archive.py" > "$temporary/install-pool-archive.py"
python3 - "$temporary/install-pool-archive.py" "$temporary/archive.tar.gz" <<'PY'
import importlib.util,sys
spec=importlib.util.spec_from_file_location('archive_installer',sys.argv[1])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
module.read_archive(sys.argv[2])
print('Committed archive payload validated.')
PY
if [[ -n $prepare_only ]]; then
  mkdir -p "$prepare_only"
  for file in archive.tar.gz install-pool-archive.py; do
    [[ ! -e $prepare_only/$file ]] || exit 1
    cp "$temporary/$file" "$prepare_only/$file"
  done
  echo "Prepared archive job $revision; no cloud changes."
  exit 0
fi
remote_dir=/tmp/xmr-archive-$revision-$(date +%s)-$$
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet --command "umask 077 && mkdir '$remote_dir'"
gcloud compute scp --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  "$temporary/archive.tar.gz" "$temporary/install-pool-archive.py" "$name:$remote_dir/"
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "sudo python3 '$remote_dir/install-pool-archive.py' '$remote_dir/archive.tar.gz' '$revision'; result=\$?; rm -rf '$remote_dir'; exit \$result"
