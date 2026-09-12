"""Read-only height cohorts for dashboard evidence; no analyzer or schema work.

The caller feeds each ring during its existing membership scan. Only small
per-era counters are retained; no second membership graph or key-image map.
"""

from bisect import bisect_right
from collections import Counter
import hashlib
import json
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).resolve().parent / "docs" / "protocol-eras.json"
CLASSIFICATION_BASIS = "mainnet_height_inferred"
COUNT_FIELDS = (
    "blocks_scanned", "transactions", "ring_members", "total_rings", "original_singleton_rings",
    "original_multimember_rings", "deterministic_resolutions",
    "deterministic_singleton_resolutions", "deterministic_multimember_resolutions",
    "hypothesis_resolutions", "unresolved_reduced", "unresolved_unchanged",
    "conflict_rings", "orphan_resolution_claims",
)
CATEGORIES = ("deterministic_resolutions", "hypothesis_resolutions",
              "unresolved_reduced", "unresolved_unchanged")


def _integer(value):
    return type(value) is int and 0 <= value <= 2**53 - 1


def mapping_fingerprint(manifest):
    """Semantic mapping hash, independent of descriptions or manifest formatting."""
    mapping = {"network": manifest["network"], "classification_basis": manifest["classification_basis"],
               "eras": [{key: era[key] for key in ("id", "start_height", "end_height", "versions")}
                        for era in manifest["eras"]]}
    return hashlib.sha256(json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_manifest(path=None):
    path = Path(path) if path is not None else DEFAULT_MANIFEST
    unavailable = {"status": "unavailable", "eras": [], "mapping_sha256": None,
                   "manifest_sha256": None, "provenance": None}
    try:
        raw = path.read_bytes()
    except OSError:
        return {**unavailable, "unavailable_reason": "manifest_unreadable"}
    file_hash = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ValueError("Unsupported manifest schema")
        if manifest.get("network") != "mainnet" or manifest.get("classification_basis") != CLASSIFICATION_BASIS:
            raise ValueError("Unsupported network/basis")
        eras = manifest.get("eras")
        if not isinstance(eras, list) or not 1 <= len(eras) <= 64:
            raise ValueError("Missing or excessive era definitions")
        identifiers, expected_start = set(), 0
        for index, era in enumerate(eras):
            if not isinstance(era, dict):
                raise ValueError("Invalid era")
            name, start, end, versions = (era.get(key) for key in ("id", "start_height", "end_height", "versions"))
            if not isinstance(name, str) or not name.strip() or name == "unknown" or name in identifiers:
                raise ValueError("Missing or duplicate era identifier")
            if not _integer(start) or start != expected_start:
                raise ValueError("Non-contiguous era starts")
            if index == len(eras) - 1:
                if end is not None:
                    raise ValueError("Final era must be open-ended")
            elif not _integer(end) or end < start:
                raise ValueError("Invalid era end")
            if not isinstance(versions, list) or not versions or any(type(value) is not int or not 1 <= value <= 255 for value in versions):
                raise ValueError("Invalid version metadata")
            if len(set(versions)) != len(versions):
                raise ValueError("Repeated version metadata")
            identifiers.add(name)
            expected_start = end + 1 if end is not None else None
        return {"status": "available", "eras": eras, "mapping_sha256": mapping_fingerprint(manifest),
                "manifest_sha256": file_hash, "provenance": manifest.get("source_pin"),
                "unavailable_reason": None}
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return {**unavailable, "manifest_sha256": file_hash, "unavailable_reason": "manifest_invalid"}


class ProtocolEraAccumulator:
    def __init__(self, conn, manifest_path=None):
        self.manifest = load_manifest(manifest_path)
        self.eras = self.manifest["eras"]
        self.starts = [era["start_height"] for era in self.eras]
        self.counts = {era["id"]: dict.fromkeys(COUNT_FIELDS, 0) for era in self.eras}
        self.counts["unknown"] = dict.fromkeys(COUNT_FIELDS, 0)
        self.unknown_reasons = Counter()
        self.rings_with_multiple_input_contexts = 0
        self.multiple_contexts_within_one_era = 0
        self._height_totals(conn, "blocks", "height", "blocks_scanned")
        self._height_totals(conn, "transactions", "block_height", "transactions")

    def classify_height(self, height):
        if not self.eras or not _integer(height):
            return "unknown"
        return self.eras[bisect_right(self.starts, height) - 1]["id"]

    def _height_totals(self, conn, table, column, field):
        # Only these fixed internal identifiers are used; bounds are bound SQL values.
        if (table, column, field) not in (("blocks", "height", "blocks_scanned"),
                                        ("transactions", "block_height", "transactions")):
            raise ValueError("Unexpected height source")
        if not self.eras:
            self.counts["unknown"][field] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return
        clauses = [f"WHEN typeof({column}) != 'integer' OR {column} < 0 OR {column} > ? THEN 'unknown'"]
        parameters = [2**53 - 1]
        for era in self.eras[:-1]:
            clauses.append(f"WHEN {column} <= ? THEN ?")
            parameters.extend((era["end_height"], era["id"]))
        expression = "CASE " + " ".join(clauses) + " ELSE ? END"
        parameters.append(self.eras[-1]["id"])
        for era_id, total in conn.execute(f"SELECT {expression} AS era_id, COUNT(*) FROM {table} GROUP BY era_id", parameters):
            self.counts[era_id][field] = total

    def add_ring(self, contexts, original_size, category, conflict, membership_count):
        """Contexts are this ring's distinct (tx hash, input index, input height)."""
        if category not in CATEGORIES:
            raise ValueError("Unknown evidence category")
        if len(contexts) > 1:
            self.rings_with_multiple_input_contexts += 1
        reason = None
        if not self.eras:
            reason = "manifest_unavailable"
        elif not contexts or any(not _integer(context[2]) for context in contexts):
            reason = "missing_or_invalid_input_height"
        else:
            era_ids = {self.classify_height(context[2]) for context in contexts}
            if len(era_ids) != 1:
                reason = "multiple_era_input_contexts"
        if reason:
            era_id = "unknown"
            self.unknown_reasons[reason] += 1
        else:
            era_id = next(iter(era_ids))
            if len(contexts) > 1:
                self.multiple_contexts_within_one_era += 1
        counts = self.counts[era_id]
        cohort = "singleton" if original_size == 1 else "multimember"
        counts["ring_members"] += membership_count
        counts["total_rings"] += 1
        counts[f"original_{cohort}_rings"] += 1
        counts[category] += 1
        if category == "deterministic_resolutions":
            counts[f"deterministic_{cohort}_resolutions"] += 1
        counts["conflict_rings"] += int(conflict)

    def report(self, summary, dataset_id=None):
        definitions = [{key: era[key] for key in ("id", "start_height", "end_height", "versions")}
                       | {"label": era.get("label", era["id"])} for era in self.eras]
        definitions.append({"id": "unknown", "label": "Unknown or ambiguous era", "start_height": None,
                            "end_height": None, "versions": []})
        rows = []
        for definition in definitions:
            row = {**definition, **self.counts[definition["id"]]}
            if row["id"] == "unknown":
                # Orphan claims have no referencing ring and never increase ring counts.
                row["orphan_resolution_claims"] = summary.get("orphan_resolution_claims", 0)
            row["fully_resolved"] = row["deterministic_resolutions"] + row["hypothesis_resolutions"]
            row["partially_reduced"] = row["unresolved_reduced"]
            row["unreduced"] = row["unresolved_unchanged"]
            rows.append(row)
        fields = (*COUNT_FIELDS, "fully_resolved", "partially_reduced", "unreduced")
        totals = {field: sum(row[field] for row in rows) for field in fields}
        checks = {field: {"era_total": total, "summary_total": summary.get(field),
                          "matches": total == summary.get(field)} for field, total in totals.items()}
        unknown = rows[-1]
        return {
            "schema_version": 1, "status": self.manifest["status"],
            "unavailable_reason": self.manifest["unavailable_reason"],
            "classification_basis": CLASSIFICATION_BASIS, "network": "mainnet",
            "network_basis": "Declared mainnet height mapping; network identity is not persisted or independently validated in this database.",
            "observed_block_versions": False,
            "height_basis": "Ring cohorts use all stored referencing transaction heights, not output creation height, scoring height, or export time.",
            "dataset_id": dataset_id, "mapping_sha256": self.manifest["mapping_sha256"],
            "manifest_sha256": self.manifest["manifest_sha256"], "manifest_path": "docs/protocol-eras.json",
            "manifest_provenance": self.manifest["provenance"],
            "rows": rows, "totals": totals,
            "coverage": {"classified_rings": totals["total_rings"] - unknown["total_rings"],
                         "unknown_rings": unknown["total_rings"], "total_rings": totals["total_rings"],
                         "classified_blocks": totals["blocks_scanned"] - unknown["blocks_scanned"],
                         "unknown_blocks": unknown["blocks_scanned"],
                         "classified_transactions": totals["transactions"] - unknown["transactions"],
                         "unknown_transactions": unknown["transactions"]},
            "unknown_reasons": dict(self.unknown_reasons),
            "context_diagnostics": {"rings_with_multiple_input_contexts": self.rings_with_multiple_input_contexts,
                                    "multiple_contexts_within_one_era": self.multiple_contexts_within_one_era},
            "reconciliation": {"matches_summary": all(check["matches"] for check in checks.values()), "fields": checks},
            "limitations": [
                "Mainnet height-derived era attribution does not validate consensus, recorded block versions, transaction RingCT type, or wallet software version.",
                "Each stored ring is counted once. Cross-era contexts or any missing/invalid input height go to unknown; multiple contexts confined to one era remain attributable there.",
                "Deterministic/hypothesis/reduction categories match the current export summary, not historical resolution availability; reductions can depend on hypotheses.",
                "Conflict counts overlap evidence categories. Original singleton counts distinguish already-unambiguous rings from original multi-member rings.",
                "Orphan resolution claims are counted only in unknown and do not add rings. Blocks and transactions are counted from their own stored heights.",
                "Absent-era counts are zero stored observations, not evidence of zero activity or successful analysis in those eras.",
            ],
        }
