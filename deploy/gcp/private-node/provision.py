#!/usr/bin/env python3
"""Plan a separate private Monero VM; --apply is required to create resources."""

import argparse
import ipaddress
import json
import re
import subprocess


APP = "tracegrove-private-node"


def suffix(value):
    return value.rsplit("/", 1)[-1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def gcloud_json(project, *args):
    result = subprocess.run(["gcloud", "compute", *args, "--project", project, "--format=json"],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def select(resources, name):
    matches = [resource for resource in resources if resource["name"] == name]
    require(len(matches) <= 1, f"Ambiguous existing resource: {name}")
    return matches[0] if matches else None


def firewall_specs(name, network, client_ip):
    common = {"network": network, "direction": "INGRESS", "targetTags": [name]}
    return [
        {**common, "name": name + "-iap", "priority": 800,
         "sourceRanges": ["35.235.240.0/20"], "allowed": [{"IPProtocol": "tcp", "ports": ["22"]}]},
        {**common, "name": name + "-rpc", "priority": 800,
         "sourceRanges": [client_ip + "/32"], "allowed": [{"IPProtocol": "tcp", "ports": ["18081"]}]},
        {**common, "name": name + "-deny-ingress", "priority": 900,
         "sourceRanges": ["0.0.0.0/0"], "denied": [{"IPProtocol": "all"}]},
    ]


def validate_rule(existing, expected):
    require(not existing.get("disabled", False), "Managed firewall rule is disabled")
    for key, value in expected.items():
        actual = suffix(existing.get(key, "")) if key == "network" else existing.get(key)
        require(actual == value, f"Existing firewall {expected['name']} differs at {key}")
    require(not existing.get("sourceTags") and not existing.get("sourceServiceAccounts")
            and not existing.get("targetServiceAccounts"), "Unexpected firewall selectors")
    require(not existing.get("denied" if "allowed" in expected else "allowed"), "Mixed firewall action")


def validate_disk(disk, size):
    require(disk.get("labels", {}).get("app") == APP, "Existing data disk is not managed by this pilot")
    require(suffix(disk.get("type", "")) == "pd-balanced", "Existing data disk has another type")
    require(int(disk.get("sizeGb", 0)) >= size, "Existing data disk is smaller than requested; resize separately")


def validate_instance(instance, args):
    require(instance.get("labels", {}).get("app") == APP, "Existing VM is not managed by this pilot")
    require(not instance.get("serviceAccounts"), "Node VM must have no service account")
    require(instance.get("deletionProtection") is True, "Node deletion protection is disabled")
    require(all(instance.get("shieldedInstanceConfig", {}).get(key) is True for key in
                ("enableSecureBoot", "enableVtpm", "enableIntegrityMonitoring")), "Shielded VM protection differs")
    scheduling = instance.get("scheduling", {})
    require(scheduling.get("provisioningModel") == "STANDARD" and scheduling.get("preemptible") is False
            and scheduling.get("automaticRestart") is True and scheduling.get("onHostMaintenance") == "MIGRATE",
            "Node must use standard non-Spot scheduling with automatic restart")
    require(suffix(instance["machineType"]) == args.machine_type, "Existing VM size differs; resize separately")
    require(instance.get("tags", {}).get("items") == [args.name], "Unexpected VM network tags")
    nics = instance.get("networkInterfaces", [])
    require(len(nics) == 1, "Expected one node network interface")
    nic = nics[0]
    require(suffix(nic["network"]) == args.network and suffix(nic["subnetwork"]) == args.subnet
            and nic.get("networkIP") == args.internal_ip, "Existing VM has different private networking")
    require(len(nic.get("accessConfigs", [])) == 1 and nic["accessConfigs"][0].get("networkTier") == "STANDARD",
            "Expected one Standard Tier external IPv4 for outbound P2P")
    disks = instance.get("disks", [])
    require(len(disks) == 2 and all(not disk.get("autoDelete") for disk in disks), "Expected two retained persistent disks")
    require(any(not disk.get("boot") and disk.get("deviceName") == "monero-data"
                and suffix(disk["source"]) == args.name + "-data" for disk in disks), "Data disk identity differs")
    require(sum(disk.get("boot") is True for disk in disks) == 1, "Expected exactly one boot disk")
    boot = next(disk for disk in disks if disk.get("boot"))
    require(suffix(boot.get("source", "")) == args.name + "-boot" and int(boot.get("diskSizeGb", 0)) >= 20,
            "Boot disk identity or capacity differs")


def validate_boot_disk(disk, name):
    require(disk is not None and suffix(disk.get("type", "")) == "pd-balanced" and int(disk.get("sizeGb", 0)) >= 20,
            "Boot disk must be at least 20 GiB pd-balanced")
    require([suffix(user) for user in disk.get("users", [])] == [name], "Boot disk attachment differs")
    require(any(suffix(license) == "debian-12-bookworm" for license in disk.get("licenses", [])),
            "Boot disk does not identify the expected Debian 12 image")


def build_plan(args):
    region = args.zone.rsplit("-", 1)[0]
    subnet = gcloud_json(args.project, "networks", "subnets", "describe", args.subnet, "--region", region)
    require(suffix(subnet["network"]) == args.network, "Subnet belongs to another network")
    network = ipaddress.ip_network(subnet["ipCidrRange"])
    require(ipaddress.ip_address(args.internal_ip) in network, "Node address is outside the subnet")
    client = gcloud_json(args.project, "instances", "describe", args.client_vm, "--zone", args.zone)
    nics = client["networkInterfaces"]
    require(len(nics) == 1 and suffix(nics[0]["network"]) == args.network, "Client VM is not in the expected VPC")
    client_ip = nics[0]["networkIP"]
    require(client_ip != args.internal_ip, "Node IP collides with collector IP")
    commands = []
    def add(*command):
        commands.append(["gcloud", "compute", *command, "--project", args.project, "--quiet"])
    rules = gcloud_json(args.project, "firewall-rules", "list")
    desired = firewall_specs(args.name, args.network, client_ip)
    managed_names = {rule["name"] for rule in desired}
    for rule in rules:
        if (suffix(rule["network"]) == args.network and rule.get("direction") == "INGRESS"
                and not rule.get("disabled") and rule.get("priority", 1000) <= 900
                and rule["name"] not in managed_names
                and (not rule.get("targetTags") or args.name in rule["targetTags"])):
            raise ValueError("Unreviewed higher-priority ingress rule could affect private-node isolation")
    for rule in desired:
        current = select(rules, rule["name"])
        if current:
            validate_rule(current, rule)
        else:
            action = ["--allow", "tcp:" + rule["allowed"][0]["ports"][0]] if "allowed" in rule else ["--deny", "all"]
            add("firewall-rules", "create", rule["name"], "--network", args.network,
                "--direction", "INGRESS", "--priority", str(rule["priority"]),
                "--source-ranges", rule["sourceRanges"][0], "--target-tags", args.name, *action)
    addresses = gcloud_json(args.project, "addresses", "list", "--filter", "region:" + region)
    require(not any(item.get("address") == args.internal_ip and item["name"] != args.name + "-internal"
                    for item in addresses), "Node address is already reserved by another resource")
    address = select(addresses, args.name + "-internal")
    if address:
        require(address.get("addressType") == "INTERNAL" and address.get("address") == args.internal_ip
                and suffix(address.get("subnetwork", "")) == args.subnet, "Existing internal address differs")
        require(all(suffix(user) == args.name for user in address.get("users", [])), "Internal address belongs to another VM")
    else:
        add("addresses", "create", args.name + "-internal", "--region", region,
            "--subnet", args.subnet, "--addresses", args.internal_ip)
    disks = gcloud_json(args.project, "disks", "list", "--filter", "zone:" + args.zone)
    disk = select(disks, args.name + "-data")
    if disk:
        validate_disk(disk, args.data_size)
        require(all(suffix(user) == args.name for user in disk.get("users", [])), "Data disk is attached to another VM")
    else:
        add("disks", "create", args.name + "-data", "--zone", args.zone,
            "--type", "pd-balanced", "--size", str(args.data_size) + "GB", "--labels", "app=" + APP)
    instances = gcloud_json(args.project, "instances", "list", "--filter", "zone:" + args.zone)
    require(not any(item["name"] != args.name and any(nic.get("networkIP") == args.internal_ip
                    and suffix(nic.get("network", "")) == args.network for nic in item.get("networkInterfaces", []))
                    for item in instances), "Node address is used by another VM")
    instance = select(instances, args.name)
    if instance:
        validate_instance(instance, args)
        validate_boot_disk(select(disks, args.name + "-boot"), args.name)
    else:
        require(select(disks, args.name + "-boot") is None, "An existing boot disk needs explicit recovery review")
        add("instances", "create", args.name, "--zone", args.zone, "--machine-type", args.machine_type,
            "--image-family", "debian-12", "--image-project", "debian-cloud", "--boot-disk-size", "20GB",
            "--boot-disk-name", args.name + "-boot", "--boot-disk-type", "pd-balanced", "--no-boot-disk-auto-delete",
            "--disk", f"name={args.name}-data,device-name=monero-data,mode=rw,boot=no,auto-delete=no",
            "--network", args.network, "--subnet", args.subnet, "--private-network-ip", args.internal_ip,
            "--network-tier", "STANDARD", "--tags", args.name, "--labels", "app=" + APP,
            "--no-service-account", "--no-scopes", "--deletion-protection", "--shielded-secure-boot",
            "--shielded-vtpm", "--shielded-integrity-monitoring",
            "--metadata", "block-project-ssh-keys=TRUE")
    return {"mode": args.mode, "private_rpc": "http://" + args.internal_ip + ":18081",
            "allowed_client_ip": client_ip, "data_disk_gib": args.data_size,
            "commands": commands, "note": "Creates resources only. Installation and empty-disk initialization are separate."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--zone", default="us-central1-a")
    parser.add_argument("--name", default="tracegrove-node")
    parser.add_argument("--network", default="tracegrove")
    parser.add_argument("--subnet", default="tracegrove")
    parser.add_argument("--client-vm", default="tracegrove")
    parser.add_argument("--internal-ip", default="10.78.0.3")
    parser.add_argument("--machine-type", choices=("e2-standard-2", "e2-standard-4"), default="e2-standard-2")
    parser.add_argument("--mode", choices=("pruned", "full"), default="pruned")
    parser.add_argument("--data-size", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for value in (args.project, args.zone, args.name, args.network, args.subnet, args.client_vm):
        require(bool(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value)), "Invalid resource name")
    ip = ipaddress.ip_address(args.internal_ip)
    require(ip.version == 4 and ip.is_private and not ip.is_loopback, "Expected a private IPv4 address")
    args.data_size = args.data_size or (300 if args.mode == "pruned" else 750)
    require(args.data_size >= (250 if args.mode == "pruned" else 625), "Disk below reviewed storage recommendation")
    plan = build_plan(args)
    print(json.dumps(plan, indent=2), flush=True)
    if args.apply:
        for command in plan["commands"]:
            subprocess.run(command, check=True)
    else:
        print("Plan only: no cloud resources were changed. Review cost before --apply.")


if __name__ == "__main__":
    main()
