"""Source-pinned taxonomy checks; no database, RPC, or ignored clone required."""

import hashlib
import json
from pathlib import Path
import re
import unittest
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
# Transcribed from the official mainnet table at the reviewed source commit.
# This independent fixture also checks CI checkouts without references/monero.
PINNED_COMMIT = "4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5"
ACTIVATIONS = [1, 1009827, 1141317, 1220516, 1288616, 1400000,
               1546000, 1685555, 1686275, 1788000, 1788720, 1978433,
               2210000, 2210720, 2688888, 2689608]


class ProtocolManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "docs/protocol-eras.json").read_text())
        cls.pin = json.loads((ROOT / "references/monero-source.json").read_text())

    def test_exact_fork_activations_match_reviewed_pin_and_genesis_normalization(self):
        manifest = self.manifest
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["network"], "mainnet")
        self.assertEqual(manifest["classification_basis"], "mainnet_height_inferred")
        self.assertEqual(manifest["source_pin"]["commit"], PINNED_COMMIT)
        self.assertEqual(manifest["source_pin"]["commit"], self.pin["commit"])
        self.assertEqual(manifest["source_pin"]["tag"], self.pin["tag"])
        forks = manifest["forks"]
        self.assertEqual([fork["version"] for fork in forks], list(range(1, 17)))
        self.assertEqual([fork["source_activation_height"] for fork in forks], ACTIVATIONS)
        self.assertEqual([fork["start_height"] for fork in forks], [0] + ACTIVATIONS[1:])
        for current, following in zip(forks, forks[1:]):
            self.assertEqual(current["end_height"], following["start_height"] - 1)
        self.assertIsNone(forks[-1]["end_height"])
        self.assertIn("Genesis height 0", manifest["height_semantics"])
        self.assertIn("not computed from hardfork timestamp", manifest["period_semantics"])

    def test_era_intervals_are_complete_disjoint_and_keep_all_fork_transitions(self):
        eras = self.manifest["eras"]
        self.assertTrue(8 <= len(eras) <= 10)
        self.assertEqual(eras[0]["start_height"], 0)
        self.assertIsNone(eras[-1]["end_height"])
        self.assertEqual(len({era["id"] for era in eras}), len(eras))
        for era in eras:
            self.assertRegex(era["id"], r"^[a-z][a-z0-9_]+$")
            self.assertIs(type(era["start_height"]), int)
            self.assertGreaterEqual(era["start_height"], 0)
            assigned = [fork for fork in self.manifest["forks"] if fork["era_id"] == era["id"]]
            self.assertEqual(era["versions"], [fork["version"] for fork in assigned])
            self.assertEqual(era["start_height"], assigned[0]["start_height"])
            self.assertEqual(era["end_height"], assigned[-1]["end_height"])
        for left, right in zip(eras, eras[1:]):
            self.assertEqual(left["end_height"] + 1, right["start_height"])
            # Both sides of every boundary have exactly one match.
            for height in (left["end_height"], right["start_height"]):
                matches = [era for era in eras if era["start_height"] <= height
                           and (era["end_height"] is None or height <= era["end_height"])]
                self.assertEqual(len(matches), 1)
        self.assertEqual([v for era in eras for v in era["versions"]], list(range(1, 17)))

    def test_every_era_separates_theory_limits_sources_and_actionable_experiments(self):
        for era in self.manifest["eras"]:
            for field in ("label", "period_label"):
                self.assertIsInstance(era[field], str)
                self.assertTrue(era[field].strip())
            for field in ("changes", "theoretical_implications", "claim_limits"):
                self.assertGreaterEqual(len(era[field]), 2)
                self.assertTrue(all(isinstance(value, str) and value.strip() for value in era[field]))
            self.assertGreaterEqual(len(era["sources"]), 2)
            self.assertTrue(era["suggested_experiments"])
            self.assertNotIn("accuracy", era)
            self.assertNotIn("security_score", era)
        for fork in self.manifest["forks"]:
            self.assertTrue(fork["transition_notes"])
            self.assertTrue(fork["sources"])

    def test_links_are_primary_sources_and_existing_public_todo_targets(self):
        known_todos = set()
        for path in (ROOT / "brain").rglob("*.md"):
            known_todos.update(re.findall(r"\*\*([A-Z]{1,4}\d+)\s*[—–-]", path.read_text()))
        entries = self.manifest["eras"] + self.manifest["forks"]
        for entry in entries:
            for source in entry["sources"]:
                self.assertTrue(source["label"])
                url = urlsplit(source["url"])
                self.assertEqual(url.scheme, "https")
                if url.hostname == "github.com":
                    self.assertTrue(url.path.startswith(f"/monero-project/monero/blob/{PINNED_COMMIT}/src/"))
                    self.assertRegex(url.fragment, r"^L\d+-L\d+$")
                else:
                    self.assertIn(url.hostname, {"getmonero.org", "www.getmonero.org", "web.getmonero.org"})
            for experiment in entry.get("suggested_experiments", []):
                self.assertIn(experiment["id"], known_todos)
                url = urlsplit(experiment["href"])
                self.assertEqual(url.path, "todos.html")
                self.assertEqual(parse_qs(url.query)["task"], [experiment["id"]])
                self.assertEqual(parse_qs(url.query)["status"], ["all"])
                self.assertTrue((ROOT / "docs" / url.path).is_file())

    def test_description_retains_meaningful_transition_exceptions(self):
        forks = {fork["version"]: " ".join(fork["transition_notes"]) for fork in self.manifest["forks"]}
        self.assertIn("still allowed", forks[8])
        self.assertIn("Borromean", forks[9])
        self.assertIn("Bulletproof2", forks[10])
        self.assertIn("rejected", forks[11])
        self.assertIn("non-miner", forks[12])
        self.assertIn("allowed", forks[13])
        self.assertIn("two explicit grandfathered", forks[14])
        self.assertIn("Grace period", forks[15])
        self.assertIn("view tags required", forks[16])

    def test_available_reference_checkout_matches_pin_and_source_ranges(self):
        source_root = ROOT / self.pin["checkout"]
        path = source_root / self.manifest["source_pin"]["hardfork_path"]
        if not path.is_file():
            self.skipTest("Optional ignored reference clone absent; pinned activation fixture was checked separately")
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.manifest["source_pin"]["hardfork_sha256"])
        section = raw.decode().split("const hardfork_t mainnet_hard_forks[] = {", 1)[1].split("};", 1)[0]
        entries = [(int(v), int(h)) for v, h in re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*,", section)]
        self.assertEqual(entries, list(enumerate(ACTIVATIONS, 1)))
        for entry in self.manifest["eras"] + self.manifest["forks"]:
            for source in entry["sources"]:
                url = urlsplit(source["url"])
                if url.hostname == "github.com":
                    rel = url.path.split(f"/blob/{PINNED_COMMIT}/", 1)[1]
                    lines = (source_root / rel).read_text().splitlines()
                    first, last = map(int, re.fullmatch(r"L(\d+)-L(\d+)", url.fragment).groups())
                    self.assertTrue(1 <= first <= last <= len(lines))


if __name__ == "__main__":
    unittest.main()
