#!/usr/bin/env bash
# Deliver the small, committed installation package through IAP; never latest.
set -euo pipefail
project=${1:?project required}
revision_arg=${2:?immutable source commit required}
initialize=${3:-}
[[ -z $initialize || $initialize == --initialize-empty-disk ]] || exit 2
[[ $project =~ ^[a-z][a-z0-9-]*$ ]] || exit 2
name=tracegrove-node
zone=us-central1-a
private_ip=10.78.0.3
repo=$(git rev-parse --show-toplevel)
revision=$(git -C "$repo" rev-parse --verify "${revision_arg}^{commit}")
[[ $revision =~ ^[a-f0-9]{40}$ ]] || exit 2
temporary=$(mktemp -d)
trap 'rm -rf "$temporary"' EXIT
git -C "$repo" archive --format=tar.gz --output "$temporary/node-package.tar.gz" "$revision" -- deploy/gcp/private-node
remote_dir=/tmp/tracegrove-node-$revision-$(date +%s)-$$
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "umask 077 && mkdir '$remote_dir'"
gcloud compute scp --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  "$temporary/node-package.tar.gz" "$name:$remote_dir/"
gcloud compute ssh "$name" --project "$project" --zone "$zone" --tunnel-through-iap --quiet \
  --command "tar -xzf '$remote_dir/node-package.tar.gz' -C '$remote_dir' && sudo bash '$remote_dir/deploy/gcp/private-node/install.sh' '$remote_dir/deploy/gcp/private-node' '$revision' pruned '$private_ip' '$initialize'; result=\$?; rm -rf '$remote_dir'; exit \$result"
