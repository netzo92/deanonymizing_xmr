"""Offline safety/contract tests; no GCP calls, data disks, services, or RPC."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


provision = load("provision")
verify = load("verify_release")


def args():
    return SimpleNamespace(project="test-project", name="tracegrove-node", zone="us-central1-a",
                           network="tracegrove", subnet="tracegrove", client_vm="tracegrove",
                           internal_ip="10.78.0.3", machine_type="e2-standard-2", mode="pruned", data_size=300)


def cloud_fixture(existing=False):
    node = {"name": "tracegrove-node", "labels": {"app": provision.APP},
            "deletionProtection": True,
            "shieldedInstanceConfig": {"enableSecureBoot": True, "enableVtpm": True, "enableIntegrityMonitoring": True},
            "scheduling": {"provisioningModel": "STANDARD", "preemptible": False, "automaticRestart": True, "onHostMaintenance": "MIGRATE"},
            "machineType": "zones/us-central1-a/machineTypes/e2-standard-2",
            "tags": {"items": ["tracegrove-node"]},
            "networkInterfaces": [{"network": "global/networks/tracegrove", "subnetwork": "regions/us-central1/subnetworks/tracegrove",
                                   "networkIP": "10.78.0.3", "accessConfigs": [{"networkTier": "STANDARD"}]}],
            "disks": [{"boot": True, "autoDelete": False, "source": "disks/tracegrove-node-boot", "diskSizeGb": "20"},
                      {"boot": False, "autoDelete": False, "deviceName": "monero-data", "source": "disks/tracegrove-node-data"}]}
    return {"subnet": {"network": "global/networks/tracegrove", "ipCidrRange": "10.78.0.0/24"},
            "client": {"networkInterfaces": [{"network": "global/networks/tracegrove", "networkIP": "10.78.0.2"}]},
            "rules": provision.firewall_specs("tracegrove-node", "tracegrove", "10.78.0.2") if existing else [],
            "addresses": [{"name": "tracegrove-node-internal", "addressType": "INTERNAL", "address": "10.78.0.3", "subnetwork": "subnetworks/tracegrove"}] if existing else [],
            "disks": [{"name": "tracegrove-node-data", "type": "diskTypes/pd-balanced", "sizeGb": "300", "labels": {"app": provision.APP}, "users": ["instances/tracegrove-node"]},
                      {"name": "tracegrove-node-boot", "type": "diskTypes/pd-balanced", "sizeGb": "20", "users": ["instances/tracegrove-node"], "licenses": ["licenses/debian-12-bookworm"]}] if existing else [],
            "instances": [node] if existing else []}


def responder(fixture):
    def read(project, *command):
        require = {("networks", "subnets"): "subnet", ("firewall-rules", "list"): "rules",
                   ("instances", "describe"): "client", ("addresses", "list"): "addresses",
                   ("disks", "list"): "disks", ("instances", "list"): "instances"}
        return copy.deepcopy(fixture[require[command[:2]]])
    return read


class PackagingTests(unittest.TestCase):
    def test_new_plan_creates_only_separate_node_resources_with_private_ingress(self):
        with patch.object(provision, "gcloud_json", side_effect=responder(cloud_fixture())):
            plan = provision.build_plan(args())
        commands = plan["commands"]
        self.assertEqual(len(commands), 6)
        vm = commands[-1]
        self.assertIn("--no-service-account", vm)
        self.assertIn("--no-boot-disk-auto-delete", vm)
        self.assertIn("name=tracegrove-node-data,device-name=monero-data,mode=rw,boot=no,auto-delete=no", vm)
        self.assertEqual(vm[vm.index("--network-tier") + 1], "STANDARD")
        self.assertEqual(plan["allowed_client_ip"], "10.78.0.2")
        rpc = next(command for command in commands if "tracegrove-node-rpc" in command)
        self.assertEqual(rpc[rpc.index("--source-ranges") + 1], "10.78.0.2/32")
        self.assertEqual(rpc[rpc.index("--allow") + 1], "tcp:18081")
        deny = next(command for command in commands if "--deny" in command)
        self.assertEqual(deny[deny.index("--deny") + 1], "all")
        self.assertFalse(any("delete" in command or "update" in command for command in commands))

    def test_matching_resources_are_reused_without_mutation(self):
        with patch.object(provision, "gcloud_json", side_effect=responder(cloud_fixture(True))):
            self.assertEqual(provision.build_plan(args())["commands"], [])

    def test_public_rpc_firewall_drift_and_earlier_broad_rule_are_rejected(self):
        for mutate in (lambda f: f["rules"][1].update(sourceRanges=["0.0.0.0/0"]),
                       lambda f: f["rules"].append({"name": "broad", "network": "networks/tracegrove", "direction": "INGRESS", "priority": 700, "allowed": [{"IPProtocol": "all"}]})):
            fixture = cloud_fixture(True); mutate(fixture)
            with patch.object(provision, "gcloud_json", side_effect=responder(fixture)):
                with self.assertRaises(ValueError): provision.build_plan(args())

    def test_wrong_or_attached_elsewhere_data_disk_is_rejected(self):
        for field, value in (("labels", {}), ("sizeGb", "100"), ("users", ["instances/production"]), ("type", "diskTypes/pd-standard")):
            fixture = cloud_fixture(True); fixture["disks"][0][field] = value
            with patch.object(provision, "gcloud_json", side_effect=responder(fixture)):
                with self.assertRaises(ValueError): provision.build_plan(args())

    def test_existing_vm_with_web_tag_service_account_or_deleting_disk_is_rejected(self):
        changes = [lambda vm: vm.update(serviceAccounts=[{"email": "unexpected"}]),
                   lambda vm: vm["tags"].update(items=["tracegrove-node", "tracegrove"]),
                   lambda vm: vm["disks"][1].update(autoDelete=True)]
        for mutate in changes:
            fixture = cloud_fixture(True); mutate(fixture["instances"][0])
            with patch.object(provision, "gcloud_json", side_effect=responder(fixture)):
                with self.assertRaises(ValueError): provision.build_plan(args())

    def test_node_client_address_collision_is_rejected(self):
        fixture = cloud_fixture(); fixture["client"]["networkInterfaces"][0]["networkIP"] = "10.78.0.3"
        with patch.object(provision, "gcloud_json", side_effect=responder(fixture)):
            with self.assertRaises(ValueError): provision.build_plan(args())

    def test_vm_protection_scheduling_and_boot_disk_drift_are_rejected(self):
        changes = [lambda f: f['instances'][0].update(deletionProtection=False),
                   lambda f: f['instances'][0]['shieldedInstanceConfig'].update(enableSecureBoot=False),
                   lambda f: f['instances'][0]['scheduling'].update(provisioningModel='SPOT'),
                   lambda f: f['disks'][1].update(type='diskTypes/pd-standard'),
                   lambda f: f['disks'][1].update(sizeGb='10'),
                   lambda f: f['disks'][1].update(users=['instances/another-vm'])]
        for mutate in changes:
            fixture = cloud_fixture(True); mutate(fixture)
            with patch.object(provision, 'gcloud_json', side_effect=responder(fixture)):
                with self.assertRaises(ValueError): provision.build_plan(args())

    def test_reserved_address_or_other_vm_collision_is_rejected(self):
        for collection, collision in [('addresses', {'name': 'other-reservation', 'address': '10.78.0.3'}),
                                      ('instances', {'name': 'other-vm', 'networkInterfaces': [{'networkIP': '10.78.0.3', 'network': 'networks/tracegrove'}]})]:
            fixture = cloud_fixture(); fixture[collection].append(collision)
            with patch.object(provision, 'gcloud_json', side_effect=responder(fixture)):
                with self.assertRaises(ValueError): provision.build_plan(args())

    def test_verified_filename_is_exact_unique_and_sha_pinned(self):
        exact = verify.ARCHIVE_SHA256 + "  " + verify.ARCHIVE
        verify.validate_signed_checksum(exact)
        for invalid in ("0" * 64 + "  " + verify.ARCHIVE, exact + "\n" + exact,
                        exact.replace("0.18.5.1", "0.18.5.2"), exact + ".malicious"):
            with self.assertRaises(ValueError): verify.validate_signed_checksum(invalid)

    def test_recorded_signature_proof_binds_vendored_files(self):
        proof = json.loads((ROOT / "verification/result.json").read_text())
        self.assertEqual(proof["source_commit"], verify.SOURCE_COMMIT)
        self.assertEqual(proof["archive_sha256"], verify.ARCHIVE_SHA256)
        self.assertEqual(proof["signer_fingerprint"], verify.FINGERPRINT)
        for filename, field in (("binaryfate.asc", "signing_key_file_sha256"), ("hashes-v0.18.5.1.asc", "signed_manifest_sha256")):
            self.assertEqual(hashlib.sha256((ROOT / "verification" / filename).read_bytes()).hexdigest(), proof[field])

    def test_modified_archive_is_rejected_after_signature_verification(self):
        with patch.object(verify, "verify_signature", return_value={"signature_valid": True}), patch.object(verify, "sha256", return_value="0" * 64):
            with self.assertRaises(ValueError): verify.verify_archive("modified.tar.bz2")


if __name__ == "__main__":
    unittest.main()
