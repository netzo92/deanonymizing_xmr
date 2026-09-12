"""Validate declared display claims against frozen JSON evidence, without writes.

The manifest is a reviewed mapping, not a source of newly inferred findings.
Only the documented, bounded expression operations below are accepted; no code
or network resources are evaluated. Use this guard before static publication.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from string import Formatter


MAX_BYTES = 8 * 1024 * 1024
MAX_CLAIMS = 500
PROSE_FIELDS = {
    "/scope/description", "/baseline_comparison/tie_method", "/baseline_comparison/interpretation",
}
PROSE_PATTERNS = (
    re.compile(r"/experiments/\d+/(title|finding|limitation|metric/label)$"),
    re.compile(r"/theoretical_conclusions/\d+/(title|statement)$"),
    re.compile(r"/next_priorities/\d+/(title|why)$"),
)
WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
         "nineteen", "twenty")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def fail(message):
    raise ValueError(message)


def regular_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        fail("Repository paths must be nonempty POSIX paths")
    path = Path(relative)
    if path.is_absolute() or any(part in {".", ".."} for part in relative.split("/")):
        fail(f"Unsafe repository path: {relative}")
    result = root / path
    if not result.resolve().is_relative_to(root.resolve()):
        fail(f"Path escapes repository: {relative}")
    if any((root / Path(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts) + 1)):
        fail(f"Source cannot be a symlink: {relative}")
    if not result.is_file() or result.stat().st_size > MAX_BYTES:
        fail(f"Missing or oversized JSON: {relative}")
    return result


def read_json(root, relative):
    raw = regular_path(root, relative).read_bytes()

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail(f"Duplicate JSON key {key!r} in {relative}")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique_pairs,
                          parse_constant=lambda value: fail(f"Nonfinite JSON value in {relative}: {value}")), raw
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"Invalid JSON in {relative}: {error}")


def pointer(document, path):
    if path == "":
        return document
    if not isinstance(path, str) or not path.startswith("/"):
        fail(f"Invalid JSON pointer: {path!r}")
    try:
        for token in path[1:].split("/"):
            if re.search(r"~(?![01])", token):
                fail(f"Invalid JSON pointer escape: {path}")
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(document, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    fail(f"Noncanonical array index in {path}")
                document = document[int(key)]
            else:
                document = document[key]
        return document
    except (KeyError, IndexError, TypeError) as error:
        fail(f"Missing JSON field {path}: {error}")


def leaves(document, prefix=""):
    if isinstance(document, dict):
        for key, value in document.items():
            escaped = key.replace("~", "~0").replace("/", "~1")
            yield from leaves(value, f"{prefix}/{escaped}")
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from leaves(value, f"{prefix}/{index}")
    else:
        yield prefix, document


def number(value):
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        value = int(value)
    if type(value) not in {int, float} or not math.isfinite(value):
        fail(f"Expected a finite number, got {value!r}")
    return value


def expression(node, artifacts, metadata, depth=0):
    if depth > 20 or not isinstance(node, dict):
        fail("Invalid or too deeply nested claim expression")
    get = lambda value: expression(value, artifacts, metadata, depth + 1)
    if set(node) == {"literal"}:
        return node["literal"]
    if set(node) == {"artifact", "pointer"}:
        if node["artifact"] not in artifacts:
            fail(f"Unknown evidence artifact: {node['artifact']}")
        return pointer(artifacts[node["artifact"]], node["pointer"])
    if set(node) == {"artifact_meta", "field"}:
        if node["artifact_meta"] not in metadata or node["field"] not in {"path", "sha256"}:
            fail("Unknown artifact metadata reference")
        return metadata[node["artifact_meta"]][node["field"]]
    operation = node.get("op")
    if operation in {"len", "sum", "max"} and set(node) == {"op", "value"}:
        value = get(node["value"])
        if not isinstance(value, (list, dict)):
            fail(f"{operation} requires a collection")
        if operation == "len":
            return len(value)
        if isinstance(value, dict) or (operation == "max" and not value):
            fail(f"{operation} requires a numeric list")
        return (sum if operation == "sum" else max)(number(item) for item in value)
    if operation == "pluck" and set(node) == {"op", "value", "field"}:
        value = get(node["value"])
        value = list(value.values()) if isinstance(value, dict) else value
        if not isinstance(value, list) or any(not isinstance(item, dict) or node["field"] not in item for item in value):
            fail("pluck requires an existing field in every row")
        return [item[node["field"]] for item in value]
    if operation == "filter" and set(node) == {"op", "value", "where"}:
        value = get(node["value"])
        where = node["where"]
        if not isinstance(value, list) or not isinstance(where, dict) or not where:
            fail("filter requires a list and explicit conditions")
        expected = {key: get(item) for key, item in where.items()}
        if any(not isinstance(item, dict) or not expected.keys() <= item.keys() for item in value):
            fail("filter fields must be present, including excluded rows")
        return [item for item in value if all(item[key] == wanted and type(item[key]) is type(wanted)
                                             for key, wanted in expected.items())]
    if operation == "constant_series_count" and set(node) == {"op", "values"}:
        values = [get(item) for item in node["values"]]
        if not values or any(not isinstance(item, list) or len(item) != 1 for item in values):
            fail("A declared constant series no longer has exactly one value")
        for item in values:
            number(item[0])
        return len(values)
    if operation == "ratio" and set(node) == {"op", "numerator", "denominator"}:
        numerator, denominator = number(get(node["numerator"])), number(get(node["denominator"]))
        if denominator <= 0:
            fail("A ratio denominator must be positive")
        return numerator / denominator
    if operation == "date_part" and set(node) == {"op", "value", "part"}:
        value = get(node["value"])
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            fail("Evidence dates require a timezone")
        stamp = stamp.astimezone(timezone.utc)
        if node["part"] == "year":
            return stamp.year
        if node["part"] == "month_name":
            return MONTHS[stamp.month - 1]
        fail("Unknown date part")
    if operation == "format" and set(node) == {"op", "template", "values"}:
        if not isinstance(node["template"], str) or not isinstance(node["values"], dict):
            fail("format requires a template and named evidence expressions")
        values = {key: get(item) for key, item in node["values"].items()}
        pieces, used = [], set()
        for literal, field, spec, conversion in Formatter().parse(node["template"]):
            pieces.append(literal)
            if field is None:
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]*", field) or field not in values or conversion:
                fail("Template fields must be plain named bindings without conversion")
            used.add(field)
            value = values[field]
            if spec in {"words", "Words"}:
                if type(value) is not int or not 0 <= value < len(WORDS):
                    fail("Word formatting supports integer counts from zero to twenty")
                pieces.append(WORDS[value].capitalize() if spec == "Words" else WORDS[value])
            elif spec in {"", ",", ".2%", ".1f", ".2f"}:
                pieces.append(format(value, spec))
            else:
                fail(f"Unsupported numerical format: {spec}")
        if used != values.keys():
            fail("Unused evidence binding in claim template")
        return "".join(pieces)
    fail(f"Unsupported claim expression: {operation!r}")


def uses_artifact(node):
    if not isinstance(node, dict):
        return False
    if "artifact" in node or "artifact_meta" in node:
        return True
    return any(uses_artifact(value) for value in node.values()) or any(
        uses_artifact(item) for value in node.values() if isinstance(value, list) for item in value)


def validate(root, manifest_path="research/conclusion-claims.json"):
    root = Path(root).resolve()
    manifest, _ = read_json(root, manifest_path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("artifacts"), dict):
        fail("Unsupported conclusion-claim manifest")
    display, _ = read_json(root, manifest["display_path"])
    if display.get("schema_version") != 1:
        fail("Unsupported research progress schema")
    artifacts, metadata = {}, manifest["artifacts"]
    if not 1 <= len(metadata) <= 20:
        fail("The manifest requires a bounded set of evidence artifacts")
    for identifier, record in metadata.items():
        artifact, raw = read_json(root, record["path"])
        if hashlib.sha256(raw).hexdigest() != record.get("sha256"):
            fail(f"Artifact hash drift: {identifier}")
        artifacts[identifier] = artifact
    claims = manifest.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
        fail("The manifest requires a bounded claim list")
    targets, identifiers = set(), set()
    for claim in claims:
        identifier, target = claim["id"], claim["target"]
        if identifier in identifiers or target in targets:
            fail(f"Duplicate claim identity or display target: {identifier}")
        identifiers.add(identifier)
        targets.add(target)
        actual = pointer(display, target)
        expected = expression(claim["expected"], artifacts, metadata)
        if actual != expected or isinstance(actual, bool) != isinstance(expected, bool):
            fail(f"Display claim drift: {identifier} ({target})")
        if not claim.get("meaning"):
            fail(f"Claim has no cohort/interpretation: {identifier}")
        if type(actual) in {int, float} and not uses_artifact(claim["expected"]):
            fail(f"Numeric claim needs an artifact field: {identifier}")
        denominator = claim.get("denominator")
        if denominator is not None:
            if not denominator.get("unit") or not uses_artifact(denominator["value"]):
                fail(f"Denominator needs units and an artifact field: {identifier}")
            if number(expression(denominator["value"], artifacts, metadata)) < 0:
                fail(f"Negative cohort denominator: {identifier}")
        elif not claim.get("denominator_reason"):
            fail(f"Explain why no denominator applies: {identifier}")
    # Every numeric scalar, exact rational integer string, and rendered prose
    # field must be covered. Array-valued claims cover their descendant leaves.
    missing = []
    for path, value in leaves(display):
        required = type(value) in {int, float} or (isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value))
        required = required or path in PROSE_FIELDS or any(pattern.fullmatch(path) for pattern in PROSE_PATTERNS)
        if path == "/schema_version":
            continue  # File-format version is checked above, not a research claim.
        if required and not any(path == target or path.startswith(target + "/") for target in targets):
            missing.append(path)
    if missing:
        fail(f"Unmapped numeric or rendered prose claims: {', '.join(missing)}")
    assertions = manifest.get("assertions", [])
    if not isinstance(assertions, list) or len(assertions) > MAX_CLAIMS:
        fail("Invalid evidence reconciliation assertions")
    for assertion in assertions:
        left = expression(assertion["left"], artifacts, metadata)
        right = expression(assertion["right"], artifacts, metadata)
        tolerance = assertion.get("absolute_tolerance")
        if tolerance is None:
            valid = left == right and isinstance(left, bool) == isinstance(right, bool)
        else:
            if type(tolerance) not in {int, float} or not 0 <= tolerance <= 1e-10:
                fail("Reconciliation tolerance must be explicit and at most 1e-10")
            valid = math.isclose(number(left), number(right), rel_tol=0, abs_tol=tolerance)
        if not valid:
            fail(f"Evidence reconciliation failed: {assertion['id']}")
    return {"schema_version": 1, "display_path": manifest["display_path"], "artifacts": len(artifacts),
            "claims": len(claims), "reconciliations": len(assertions)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", default="research/conclusion-claims.json")
    args = parser.parse_args(argv)
    try:
        result = validate(args.root, args.manifest)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"Conclusion claim validation failed: {error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
