"""Export recorded TODO transitions from complete local first-parent Git history.

This command reads Markdown and Git objects only. Dates are committer timestamps,
not independently known creation/completion times. A git archive can validate a
committed snapshot with --validate-only, but cannot reconstruct its history.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

from brain_export import REPOSITORY, prose_only


MAX_COMMITS = 10_000
MAX_PAGES = 256
MAX_PAGE_BYTES = 400_000
MAX_HISTORY_BYTES = 64 * 1024 * 1024
MAX_TASKS = 5_000
MAX_TASK_HISTORIES = 20_000
MAX_EVENTS = 100_000
SHA = re.compile(r"[a-f0-9]{40}")
TODO = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$", re.MULTILINE)
CODE = re.compile(r"^([A-Z]{1,4}\d+)\s*[—:-]")
EVENT_KINDS = {"first_recorded", "closed", "reopened", "removed", "reintroduced"}


def digest(manifest):
    """Use the same whole-brain content fingerprint as brain_export.py."""
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def plain_markdown(value):
    value = re.sub(r"\[([^\]]+)\]\([^\n]*?\)", r"\1", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    return re.sub(r"`([^`]+)`", r"\1", value).strip()


def parse_tasks(pages):
    """Match the brain checkbox scope, using path+code or normalized title."""
    tasks = {}
    for path, raw in sorted(pages.items()):
        content = raw.decode("utf-8")
        content = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", content, flags=re.DOTALL)
        for match in TODO.finditer(prose_only(content)):
            text = match.group(2).strip()
            heading = re.match(r"\*\*(.+?)\*\*", text)
            title = plain_markdown(heading.group(1) if heading else text)
            code_match = CODE.match(title)
            code = code_match.group(1) if code_match else None
            normalized = " ".join(unicodedata.normalize("NFKC", title).casefold().split())
            suffix = code or "title-" + hashlib.sha256(normalized.encode()).hexdigest()[:16]
            identifier = f"{path}#{suffix}"
            if identifier in tasks:
                raise ValueError(f"Ambiguous duplicate task identity: {identifier}")
            done = match.group(1).lower() == "x"
            tasks[identifier] = {
                "id": identifier, "note_id": path, "code": code, "title": title,
                "text": text, "done": done, "status": "completed" if done else "open",
            }
    if len(tasks) > MAX_TASKS:
        raise ValueError("Too many TODOs in one Markdown snapshot")
    return tasks


def current_pages(root):
    directory = root / "brain"
    if directory.is_symlink() or not (directory / "index.md").is_file():
        raise ValueError("Brain must be a real directory with index.md")
    paths = sorted(directory.rglob("*.md"))
    if not paths or len(paths) > MAX_PAGES:
        raise ValueError("Brain must contain at most 256 Markdown pages")
    pages = {}
    for path in paths:
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Brain pages must remain inside their directory")
        if not path.is_file() or path.stat().st_size > MAX_PAGE_BYTES:
            raise ValueError(f"Invalid or oversized brain page: {path}")
        pages[path.relative_to(root).as_posix()] = path.read_bytes()
    return pages


def manifest_for(pages):
    return {path: hashlib.sha256(raw).hexdigest() for path, raw in sorted(pages.items())}


class Git:
    def __init__(self, root):
        self.root = root

    def run(self, *arguments):
        try:
            result = subprocess.run(
                ["git", "--no-replace-objects", *arguments], cwd=self.root,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"},
                capture_output=True, check=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("Complete local Git history is required to build task activity") from error
        if len(result.stdout) > MAX_HISTORY_BYTES:
            raise ValueError("Git output exceeds the bounded history size")
        return result.stdout

    def history(self):
        if Path(self.run("rev-parse", "--show-toplevel").decode().strip()).resolve() != self.root:
            raise ValueError("--root must be the Git repository root")
        if self.run("rev-parse", "--is-shallow-repository").strip() != b"false":
            raise ValueError("Shallow Git history cannot establish first-recorded transitions")
        # Local grafts can hide ancestry even when a repository is not shallow.
        grafts = Path(self.run("rev-parse", "--git-path", "info/grafts").decode().strip())
        if not grafts.is_absolute():
            grafts = self.root / grafts
        if grafts.exists() and grafts.read_text().strip():
            raise ValueError("Grafted Git history is not supported")
        rows = self.run("log", "--first-parent", "--reverse", f"--max-count={MAX_COMMITS + 1}",
                        "--format=%H%x09%ct%x09%P", "HEAD").decode().splitlines()
        if not rows or len(rows) > MAX_COMMITS:
            raise ValueError("Git history is empty or exceeds the commit limit")
        history = []
        previous = None
        for row in rows:
            commit, seconds, parents = row.split("\t")
            parent_list = parents.split()
            if not SHA.fullmatch(commit) or (parent_list[0] if parent_list else None) != previous:
                raise ValueError("Git first-parent history must reach an unbroken root")
            at = datetime.fromtimestamp(int(seconds), timezone.utc).isoformat().replace("+00:00", "Z")
            history.append({"commit": commit, "at": at})
            previous = commit
        return history

    def pages(self, commit, cache):
        pages = {}
        tree = self.run("ls-tree", "-rz", "--full-tree", commit, "--", "brain")
        for entry in tree.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            path = raw_path.decode("utf-8")
            if not path.endswith(".md"):
                continue
            mode, kind, raw_blob = metadata.decode().split()
            if mode not in ("100644", "100755") or kind != "blob":
                raise ValueError(f"Historical brain page is not a regular file: {path}")
            if raw_blob not in cache:
                size = int(self.run("cat-file", "-s", raw_blob))
                if size > MAX_PAGE_BYTES or sum(map(len, cache.values())) + size > MAX_HISTORY_BYTES:
                    raise ValueError("Historical Markdown exceeds the bounded size")
                cache[raw_blob] = self.run("cat-file", "blob", raw_blob)
            pages[path] = cache[raw_blob]
        if len(pages) > MAX_PAGES:
            raise ValueError("Historical snapshot has too many Markdown pages")
        return pages


def summary_for(tasks, events):
    return {**{status: sum(task["status"] == status for task in tasks)
               for status in ("open", "completed", "removed")},
            "events": len(events), "pending": sum(task["pending_change"] for task in tasks)}


def build_activity(root):
    root = Path(root).resolve()
    pages = current_pages(root)
    current = parse_tasks(pages)
    git = Git(root)
    history = git.history()
    records, events, previous, cache = {}, [], {}, {}
    head_pages = {}
    previous_pages, note_events = {}, []
    for point in history:
        head_pages = git.pages(point["commit"], cache)
        for path in sorted(previous_pages.keys() | head_pages.keys()):
            before_raw, after_raw = previous_pages.get(path), head_pages.get(path)
            if before_raw != after_raw:
                note_events.append({**point, "note_id": path,
                    "kind": "note_removed" if after_raw is None else "note_added" if before_raw is None else "note_updated",
                    "content_sha256": hashlib.sha256(after_raw).hexdigest() if after_raw is not None else None})
                if len(note_events) > MAX_EVENTS:
                    raise ValueError("Note change history exceeds the bounded size")
        previous_pages = head_pages
        observed = parse_tasks(head_pages)
        for identifier in sorted(previous.keys() | observed.keys()):
            before, after = previous.get(identifier), observed.get(identifier)
            if before is None:
                kind = "reintroduced" if identifier in records else "first_recorded"
            elif after is None:
                kind = "removed"
            elif before["done"] != after["done"]:
                kind = "closed" if after["done"] else "reopened"
            else:
                kind = None
            task = after or before
            if identifier not in records:
                records[identifier] = {**task, "first_recorded": None, "events": []}
            record = records[identifier]
            if after:
                record.update(after)
            else:
                record.update(status="removed", done=None)
            if kind:
                event = {"kind": kind, **point, "status": record["status"], "title": task["title"]}
                if kind == "first_recorded":
                    record["first_recorded"] = {**point, "status": record["status"]}
                record["events"].append(event)
                events.append({**event, "task_id": identifier, "note_id": task["note_id"],
                               "code": task["code"]})
                if len(events) > MAX_EVENTS or len(records) > MAX_TASK_HISTORIES:
                    raise ValueError("Task lifecycle history exceeds the bounded size")
        previous = observed
    for identifier in sorted(records.keys() | current.keys()):
        actual = current.get(identifier)
        if identifier not in records:
            records[identifier] = {**actual, "first_recorded": None, "events": []}
            if len(records) > MAX_TASK_HISTORIES:
                raise ValueError("Task lifecycle history exceeds the bounded size")
        record = records[identifier]
        record["pending_change"] = actual != previous.get(identifier)
        if actual:
            record.update(actual)
        else:
            record.update(status="removed", done=None)
        last = record["events"][-1] if record["events"] else None
        record["last_transition"] = ({key: last[key] for key in ("kind", "commit", "at", "status")}
                                     if last else None)
    tasks = [records[key] for key in sorted(records)]
    manifest = manifest_for(pages)
    changed = manifest != manifest_for(head_pages)
    return {
        "schema_version": 1, "repository_url": REPOSITORY,
        "source_commit": None if changed else history[-1]["commit"],
        "history": {"head_commit": history[-1]["commit"], "head_at": history[-1]["at"],
                    "root_commit": history[0]["commit"], "commit_count": len(history),
                    "mode": "first_parent", "complete": True},
        "content_sha256": digest(manifest), "source_manifest": manifest,
        "working_tree_changes": changed, "tasks": tasks, "events": events, "note_events": note_events,
        "summary": summary_for(tasks, events),
    }


def validate_snapshot(root, payload):
    """Validate current source content and task states without requiring Git.

    The archive's release commit may follow history.head_commit by a generated
    artifact-only commit. Content hashes, rather than HEAD equality, bind it.
    Git event authenticity still relies on the reviewed committed artifact.
    """
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    def point_valid(point):
        if not isinstance(point, dict) or not isinstance(point.get("commit"), str) or not SHA.fullmatch(point["commit"]):
            return False
        try:
            at = point["at"]
            return (isinstance(at, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", at)
                    and datetime.fromisoformat(at[:-1] + "+00:00").utcoffset().total_seconds() == 0)
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    require(isinstance(payload, dict) and payload.get("schema_version") == 1, "Unsupported task activity schema")
    pages = current_pages(Path(root).resolve())
    manifest = manifest_for(pages)
    require(payload.get("source_manifest") == manifest and payload.get("content_sha256") == digest(manifest),
            "Task activity is stale; rebuild after every Markdown brain change")
    history = payload.get("history")
    require(isinstance(history, dict) and history.get("mode") == "first_parent" and history.get("complete") is True,
            "Task activity requires complete first-parent history metadata")
    require(point_valid({"commit": history.get("head_commit"), "at": history.get("head_at")})
            and isinstance(history.get("root_commit"), str) and SHA.fullmatch(history["root_commit"])
            and type(history.get("commit_count")) is int and 0 < history["commit_count"] <= MAX_COMMITS,
            "Invalid task activity Git history metadata")
    require(type(payload.get("working_tree_changes")) is bool and payload.get("source_commit") ==
            (None if payload["working_tree_changes"] else history["head_commit"]), "Invalid source revision metadata")
    require(payload.get("repository_url") == REPOSITORY, "Unexpected task repository URL")
    tasks, events = payload.get("tasks"), payload.get("events")
    require(isinstance(tasks, list) and isinstance(events, list) and len(tasks) <= MAX_TASK_HISTORIES
            and len(events) <= MAX_EVENTS,
            "Invalid task activity lists")
    current = parse_tasks(pages)
    by_id = {}
    projected = []
    for task in tasks:
        require(isinstance(task, dict) and isinstance(task.get("id"), str) and task["id"] not in by_id,
                "Invalid or duplicate task activity identity")
        identifier = task["id"]
        by_id[identifier] = task
        note_id = task.get("note_id")
        require(isinstance(note_id, str) and note_id.startswith("brain/") and note_id.endswith(".md")
                and all(part not in ("", ".", "..") for part in note_id.split("/"))
                and isinstance(task.get("text"), str) and "\n" not in task["text"], "Invalid task note identity")
        identity = parse_tasks({note_id: ("- [ ] " + task["text"]).encode()})
        require(identifier in identity and all(task.get(key) == identity[identifier][key]
                for key in ("id", "note_id", "code", "title", "text")), "Task identity does not match its recorded text")
        require(task.get("status") in ("open", "completed", "removed") and type(task.get("pending_change")) is bool,
                "Invalid current task state")
        if identifier in current:
            require(type(task.get("done")) is bool and all(task.get(key) == value for key, value in current[identifier].items()),
                    f"Task activity does not match current checkbox: {identifier}")
        else:
            require(task["status"] == "removed" and task.get("done") is None,
                    f"Task activity references a missing current checkbox: {identifier}")
        require(isinstance(task.get("events"), list), "Invalid per-task events")
        state = None
        seen_commits = set()
        for index, event in enumerate(task["events"]):
            require(point_valid(event) and event.get("kind") in EVENT_KINDS
                    and isinstance(event.get("title"), str), "Invalid task event")
            require(event["commit"] not in seen_commits, "Duplicate task transition in one commit")
            seen_commits.add(event["commit"])
            kind, status = event["kind"], event.get("status")
            valid = ((index == 0 and kind == "first_recorded" and status in ("open", "completed"))
                     or (index > 0 and kind == "closed" and state == "open" and status == "completed")
                     or (index > 0 and kind == "reopened" and state == "completed" and status == "open")
                     or (index > 0 and kind == "removed" and state in ("open", "completed") and status == "removed")
                     or (index > 0 and kind == "reintroduced" and state == "removed" and status in ("open", "completed")))
            require(valid, "Invalid task lifecycle transition")
            state = status
            projected.append({**event, "task_id": identifier, "note_id": task.get("note_id"), "code": task.get("code")})
        first, last = (task["events"][0], task["events"][-1]) if task["events"] else (None, None)
        require(task.get("first_recorded") == ({key: first[key] for key in ("commit", "at", "status")} if first else None)
                and task.get("last_transition") == ({key: last[key] for key in ("kind", "commit", "at", "status")} if last else None),
                "Task transition summaries do not reconcile")
        require(task["pending_change"] or state == task["status"], "Committed task status differs from its history")
    require(set(current).issubset(by_id), "Task activity omits current checkboxes")
    require(all(isinstance(event, dict) for event in events), "Invalid activity feed")
    require(sorted(json.dumps(event, sort_keys=True) for event in projected) ==
            sorted(json.dumps(event, sort_keys=True) for event in events), "Activity feed does not reconcile with task histories")
    note_events = payload.get("note_events", [])
    require(isinstance(note_events, list) and len(note_events) <= MAX_EVENTS, "Invalid note history")
    last_notes, note_keys = {}, set()
    for event in note_events:
        require(point_valid(event) and isinstance(event.get("note_id"), str) and event["note_id"].startswith("brain/")
                and ".." not in Path(event["note_id"]).parts and event["note_id"].endswith(".md")
                and event.get("kind") in {"note_added", "note_updated", "note_removed"}, "Invalid note event")
        key = (event["note_id"], event["commit"])
        require(key not in note_keys, "Duplicate note change")
        note_keys.add(key)
        value = event.get("content_sha256")
        require(value is None if event["kind"] == "note_removed" else isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value), "Invalid note content hash")
        last_notes[event["note_id"]] = value
    if note_events and not payload["working_tree_changes"]:
        require({key: value for key, value in last_notes.items() if value is not None} == payload["source_manifest"], "Note history does not match current Markdown")
    require(payload.get("summary") == summary_for(tasks, events), "Task activity totals do not reconcile")
    require(payload["working_tree_changes"] or not any(task["pending_change"] for task in tasks),
            "Pending task changes require a dirty source snapshot")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, default=Path("docs/task-activity.json"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Rebuild from Git and compare the committed snapshot")
    mode.add_argument("--validate-only", action="store_true", help="Validate current sources and snapshot without Git")
    args = parser.parse_args()
    try:
        if args.validate_only or args.check:
            stored = json.loads(args.output.read_text())
            validate_snapshot(args.root, stored)
            if args.check:
                rebuilt = build_activity(args.root)
                # A follow-up commit containing only generated artifacts does not
                # invalidate recorded task history. New transitions still do.
                keys = ("schema_version", "repository_url", "content_sha256", "source_manifest",
                        "working_tree_changes", "tasks", "events", "note_events", "summary")
                history = Git(args.root.resolve()).history()
                first_parent = {point["commit"]: index for index, point in enumerate(history)}
                recorded_index = first_parent.get(stored["history"]["head_commit"])
                history_matches = (recorded_index is not None
                    and stored["history"]["root_commit"] == history[0]["commit"]
                    and stored["history"]["commit_count"] == recorded_index + 1
                    and stored["history"]["head_at"] == history[recorded_index]["at"]
                    and all(first_parent.get(event["commit"], MAX_COMMITS) <= recorded_index for event in stored["events"]))
                if not history_matches or any(stored.get(key) != rebuilt[key] for key in keys):
                    raise ValueError("Task activity differs from Git history; run python task_activity.py")
            print("Task activity matches the Markdown sources" + (" and Git history" if args.check else ""))
            return
        payload = build_activity(args.root)
        validate_snapshot(args.root, payload)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.is_symlink():
            raise ValueError("Task activity output cannot be a symlink")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, args.output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"Exported {len(payload['tasks'])} task histories and {len(payload['events'])} recorded events")
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
