"""Build a public, navigable snapshot of the repository's Markdown knowledge."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit


REPOSITORY = "https://github.com/netzo92/deanonymizing_xmr"
LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^\s)]+)\)")


def prose_only(content):
    return re.sub(r"^(`{3,}|~{3,}).*?^\1\s*$", "", content,
                  flags=re.MULTILINE | re.DOTALL)


def source_revision(root):
    release = root / "REVISION"
    if release.is_file() and re.fullmatch(r"[a-f0-9]{40}", release.read_text().strip()):
        return release.read_text().strip()
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "brain", "brain_export.py"],
            cwd=root, text=True, capture_output=True, check=True, timeout=5,
        ).stdout
        if dirty:
            return None
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                              capture_output=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_brain(root):
    root = Path(root).resolve()
    directory = root / "brain"
    pages = sorted(directory.rglob("*.md"))
    if not pages or len(pages) > 256 or not (directory / "index.md").is_file():
        raise ValueError("Brain must contain an index and at most 256 Markdown pages")
    nodes, manifest = [], {}
    for page in pages:
        if page.is_symlink() or not page.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"Brain page must be inside its directory: {page}")
        raw = page.read_bytes()
        if len(raw) > 400_000:
            raise ValueError(f"Brain page is too large: {page}")
        identifier = page.relative_to(root).as_posix()
        manifest[identifier] = hashlib.sha256(raw).hexdigest()
        content = raw.decode("utf-8")
        metadata = {}
        frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*\n", content, flags=re.DOTALL)
        if frontmatter:
            for line in frontmatter.group(1).splitlines():
                key, separator, value = line.partition(":")
                if separator:
                    metadata[key.strip()] = value.strip().strip('"\'')
            content = content[frontmatter.end():]
        title = re.search(r"^#\s+(.+)$", content, flags=re.MULTILINE)
        prose = prose_only(content)
        todos = [{"text": match.group(2).strip(), "done": match.group(1).lower() == "x"}
                 for match in re.finditer(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$", prose,
                                          flags=re.MULTILINE)]
        nodes.append({
            "id": identifier, "path": identifier,
            "title": title.group(1).strip() if title else page.stem.replace("-", " "),
            "summary": metadata.get("summary", ""),
            "branch": page.relative_to(directory).parts[0] if page.parent != directory else "root",
            "status": metadata.get("status", "unknown"), "reviewed": metadata.get("reviewed"),
            "content": content, "todos": todos, "sources": [], "links": [],
        })
    identifiers = {node["id"] for node in nodes}
    edges = []
    hierarchy_pairs = set()
    for node in nodes:
        path = Path(node["id"])
        if node["id"] == "brain/index.md":
            continue
        parent = path.parent.parent / "index.md" if path.name == "index.md" else path.parent / "index.md"
        if parent.as_posix() not in identifiers:
            raise ValueError(f"Missing parent index for {node['id']}")
        edges.append({"source": parent.as_posix(), "target": node["id"], "kind": "hierarchy"})
        hierarchy_pairs.add(frozenset((parent.as_posix(), node["id"])))
    for node in nodes:
        seen_sources = set()
        for label, destination in LINK.findall(prose_only(node["content"])):
            parsed = urlsplit(destination)
            if parsed.scheme:
                if parsed.scheme in ("https", "http") and destination not in seen_sources:
                    node["sources"].append({"label": label, "url": destination})
                    seen_sources.add(destination)
                continue
            if parsed.netloc or not parsed.path:
                continue
            target = (root / node["path"]).parent / unquote(parsed.path)
            target = target.resolve()
            if not target.is_relative_to(root) or not target.exists():
                raise ValueError(f"Unresolved repository link in {node['id']}: {destination}")
            relative = target.relative_to(root).as_posix()
            if relative in identifiers:
                if relative not in node["links"] and relative != node["id"]:
                    node["links"].append(relative)
                    if frozenset((relative, node["id"])) not in hierarchy_pairs:
                        edges.append({"source": node["id"], "target": relative, "kind": "reference"})
            else:
                url = f"{REPOSITORY}/blob/main/{relative}" + (f"#{parsed.fragment}" if parsed.fragment else "")
                if url not in seen_sources:
                    node["sources"].append({"label": label, "url": url})
                    seen_sources.add(url)
    reachable, pending = set(), ["brain/index.md"]
    by_id = {node["id"]: node for node in nodes}
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(by_id[current]["links"])
    if reachable != identifiers:
        raise ValueError("Brain pages are not reachable from index links: " + ", ".join(sorted(identifiers - reachable)))
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_revision(root), "repository_url": REPOSITORY,
        "root_id": "brain/index.md", "nodes": nodes, "edges": edges,
        "content_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "source_manifest": manifest,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, default=Path("docs/brain.json"))
    parser.add_argument("--check", action="store_true", help="Fail if the exported brain content is stale")
    args = parser.parse_args()
    payload = build_brain(args.root)
    if args.check:
        stored = json.loads(args.output.read_text())
        for key in ("schema_version", "content_sha256", "nodes", "edges"):
            if stored.get(key) != payload[key]:
                raise SystemExit("Brain export is stale; run python brain_export.py")
        print("Brain export matches the Markdown sources")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        temporary = Path(stream.name)
    try:
        temporary.chmod(0o644)
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Exported {len(payload['nodes'])} brain pages to {args.output}")


if __name__ == "__main__":
    main()
