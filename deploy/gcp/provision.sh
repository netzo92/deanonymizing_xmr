#!/usr/bin/env bash
# Creates a dedicated Compute Engine collector host. Does not deploy local code.
set -euo pipefail
project=''
zone=us-central1-a
name=xmr-research
machine_type=e2-standard-2
disk_size=100
network=xmr-research
subnet=xmr-research
subnet_range=10.78.0.0/24
usage() {
  cat <<'EOF'
Usage: deploy/gcp/provision.sh --project PROJECT [options]
  --zone ZONE                 default: us-central1-a
  --name NAME                 default: xmr-research
  --machine-type TYPE         default: e2-standard-2 (2 vCPU, 8 GB)
  --disk-size GB              default: 100; retained if VM is deleted
  --network NAME              default: xmr-research; dedicated custom VPC
  --subnet NAME               default: xmr-research; in the zone's region
  --subnet-range CIDR          default: 10.78.0.0/24
Existing matching resources are reused. Existing VMs are not resized/replaced.
Deploy a specific source commit afterwards with deploy/gcp/deploy.sh.
EOF
}
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) project=${2:?}; shift 2 ;;
    --zone) zone=${2:?}; shift 2 ;;
    --name) name=${2:?}; shift 2 ;;
    --machine-type) machine_type=${2:?}; shift 2 ;;
    --disk-size) disk_size=${2:?}; shift 2 ;;
    --network) network=${2:?}; shift 2 ;;
    --subnet) subnet=${2:?}; shift 2 ;;
    --subnet-range) subnet_range=${2:?}; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n $project ]] || { usage >&2; exit 2; }
for value in "$project" "$zone" "$name" "$machine_type" "$network" "$subnet"; do
  [[ $value =~ ^[a-z][a-z0-9-]*$ ]] || { echo 'Invalid Google Cloud resource name.' >&2; exit 2; }
done
[[ $disk_size =~ ^[0-9]+$ && $disk_size -ge 20 ]] || { echo 'Disk size must be at least 20 GB.' >&2; exit 2; }
region=${zone%-*}
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
command -v gcloud >/dev/null || { echo 'Install the Google Cloud CLI first.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Python 3 is required for deployment validation.' >&2; exit 1; }
gcloud services enable compute.googleapis.com iap.googleapis.com --project "$project" --quiet

if ! gcloud compute networks describe "$network" --project "$project" >/dev/null 2>&1; then
  gcloud compute networks create "$network" --project "$project" --subnet-mode custom --quiet
fi
if ! gcloud compute networks subnets describe "$subnet" --region "$region" --project "$project" >/dev/null 2>&1; then
  gcloud compute networks subnets create "$subnet" --project "$project" --region "$region" \
    --network "$network" --range "$subnet_range" --quiet
fi
actual_network=$(gcloud compute networks subnets describe "$subnet" --region "$region" --project "$project" --format='value(network)')
[[ ${actual_network##*/} == "$network" ]] || { echo 'Existing subnet belongs to a different network.' >&2; exit 1; }

for rule_kind in web iap; do
  rule_name=$name-$rule_kind
  if [[ $rule_kind == web ]]; then
    source_range=0.0.0.0/0
    allowed=tcp:80
  else
    source_range=35.235.240.0/20
    allowed=tcp:22
  fi
  if ! gcloud compute firewall-rules describe "$rule_name" --project "$project" >/dev/null 2>&1; then
    gcloud compute firewall-rules create "$rule_name" --project "$project" --network "$network" \
      --direction INGRESS --priority 1000 --allow "$allowed" --source-ranges "$source_range" \
      --target-tags "$name" --quiet
  else
    gcloud compute firewall-rules describe "$rule_name" --project "$project" --format=json |
      python3 -c '
import json, sys
rule = json.load(sys.stdin)
network, tag, source, allowed = sys.argv[1:]
protocol, port = allowed.split(":")
if not (rule["network"].rsplit("/", 1)[-1] == network
        and rule.get("direction") == "INGRESS"
        and not rule.get("disabled", False)
        and rule.get("targetTags") == [tag]
        and rule.get("sourceRanges") == [source]
        and rule.get("allowed") == [{"IPProtocol": protocol, "ports": [port]}]):
    raise SystemExit("Existing firewall does not match the required network, ports or sources.")
' "$network" "$name" "$source_range" "$allowed"
  fi
done
if ! gcloud compute addresses describe "$name-ip" --region "$region" --project "$project" >/dev/null 2>&1; then
  gcloud compute addresses create "$name-ip" --region "$region" --project "$project" --network-tier PREMIUM --quiet
fi
address=$(gcloud compute addresses describe "$name-ip" --region "$region" --project "$project" --format='value(address)')
if gcloud compute instances describe "$name" --zone "$zone" --project "$project" >/dev/null 2>&1; then
  gcloud compute instances describe "$name" --zone "$zone" --project "$project" --format=json |
    python3 -c '
import json, sys
instance = json.load(sys.stdin)
network, subnet, address, tag = sys.argv[1:]
interfaces = instance.get("networkInterfaces", [])
if not (instance.get("labels", {}).get("app") == "xmr-research"
        and not instance.get("serviceAccounts") and len(interfaces) == 1
        and interfaces[0]["network"].rsplit("/", 1)[-1] == network
        and interfaces[0]["subnetwork"].rsplit("/", 1)[-1] == subnet
        and interfaces[0].get("accessConfigs", [{}])[0].get("natIP") == address
        and tag in instance.get("tags", {}).get("items", [])):
    raise SystemExit("Existing instance does not match the required project deployment configuration.")
' "$network" "$subnet" "$address" "$name"
  echo "Reusing $name; machine, disk, network and metadata are not changed."
else
  gcloud compute instances create "$name" --project "$project" --zone "$zone" \
    --machine-type "$machine_type" --image-project debian-cloud --image-family debian-12 \
    --boot-disk-type pd-balanced --boot-disk-size "${disk_size}GB" --no-boot-disk-auto-delete \
    --network "$network" --subnet "$subnet" --address "$address" --network-tier PREMIUM \
    --no-service-account --no-scopes --tags "$name" --labels app=xmr-research \
    --deletion-protection --shielded-secure-boot \
    --metadata block-project-ssh-keys=TRUE \
    --metadata-from-file "startup-script=$script_dir/startup.sh" --quiet
fi
echo "Reserved dashboard address: http://$address/"
echo "Bootstrap logs: gcloud compute ssh $name --project $project --zone $zone --tunnel-through-iap --command 'sudo journalctl -u google-startup-scripts.service --no-pager -n 50'"
echo 'Next: deploy/gcp/deploy.sh --project PROJECT --commit FULL_COMMIT --env-file LOCAL_ENV_FILE'
