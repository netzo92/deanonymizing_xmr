import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from task_activity import build_activity, parse_tasks, validate_snapshot


class TaskActivityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        (self.root / "brain").mkdir()
        self.note = self.root / "brain/index.md"
        self.tick = 0

    def git(self, *arguments, **kwargs):
        environment = {
            **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        environment.update(kwargs.pop("env", {}))
        return subprocess.run(["git", *arguments], cwd=self.root, env=environment,
                              check=True, capture_output=True, text=True, **kwargs).stdout.strip()

    def commit(self, content=None):
        if content is not None:
            self.note.write_text("# Brain\n\n" + content)
        self.git("add", ".")
        self.tick += 1
        at = f"2026-09-11T10:{self.tick:02d}:00-07:00"
        self.git("commit", "-q", "--allow-empty", "-m", f"Fixture {self.tick}",
                 env={"GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at})
        return self.git("rev-parse", "HEAD")

    def build(self):
        payload = build_activity(self.root)
        self.assertEqual(validate_snapshot(self.root, payload), payload)
        return payload

    def cli(self, *arguments):
        script = Path(__file__).resolve().parents[1] / "task_activity.py"
        return subprocess.run([sys.executable, str(script), "--root", str(self.root),
                               "--output", str(self.root / "activity.json"), *arguments],
                              capture_output=True, text=True)

    def test_added_closed_reopened_removed_reintroduced_have_distinct_events(self):
        self.commit("")
        first = self.commit("- [ ] **D1 — A task.** Before.\n")
        closed = self.commit("- [x] **D1 — A task.** Measured result.\n")
        self.commit("- [ ] **D1 — A task.** Reopened.\n")
        self.commit("")
        last = self.commit("- [x] **D1 — A task.** Reintroduced already checked.\n")
        payload = self.build()
        task = payload["tasks"][0]
        self.assertEqual(task["id"], "brain/index.md#D1")
        self.assertEqual([event["kind"] for event in task["events"]],
                         ["first_recorded", "closed", "reopened", "removed", "reintroduced"])
        self.assertEqual(task["first_recorded"], {"commit": first, "at": "2026-09-11T17:02:00Z", "status": "open"})
        self.assertEqual(task["events"][1]["commit"], closed)
        self.assertEqual(task["last_transition"]["commit"], last)
        self.assertFalse(task["pending_change"])
        self.assertEqual(payload["summary"], {"open": 0, "completed": 1, "removed": 0, "events": 5, "pending": 0})

    def test_imported_completed_task_is_not_an_observed_closure(self):
        self.commit("- [x] **EA1 — Audit.** Imported result.\n")
        task = self.build()["tasks"][0]
        self.assertEqual(task["first_recorded"]["status"], "completed")
        self.assertEqual([event["kind"] for event in task["events"]], ["first_recorded"])

    def test_note_prose_edits_have_dates_without_inventing_task_transitions(self):
        first = self.commit("Initial prose.\n")
        edited = self.commit("Measured conclusion changed.\n")
        self.commit()
        payload = self.build()
        self.assertEqual(payload['events'], [])
        self.assertEqual([event['kind'] for event in payload['note_events']], ['note_added','note_updated'])
        self.assertEqual([event['commit'] for event in payload['note_events']], [first,edited])
        self.assertEqual(payload['note_events'][-1]['at'], '2026-09-11T17:02:00Z')
        broken = copy.deepcopy(payload)
        broken['note_events'][-1]['content_sha256'] = '0' * 64
        self.assertRaisesRegex(ValueError, 'Note history', validate_snapshot, self.root, broken)

    def test_body_edits_and_line_moves_keep_stable_title_or_code_identity(self):
        self.commit("- [ ] **Research question.** Original body.\n- [ ] **D1 — Original title.** Body.\n")
        before = self.build()
        self.commit("New prose.\n\n- [ ] **D1 — Renamed with same code.** New body.\n\n\n"
                    "- [ ] **Research question.** Completely revised body.\n")
        after = self.build()
        self.assertEqual([task["id"] for task in before["tasks"]], [task["id"] for task in after["tasks"]])
        self.assertEqual(before["events"], after["events"])
        self.assertIn("Completely revised", next(task["text"] for task in after["tasks"] if task["code"] is None))

    def test_title_or_path_rename_is_removal_and_new_identity_without_inferred_link(self):
        self.commit("- [ ] **An old title.** Body.\n- [ ] **D1 — Stable within a note.** Body.\n")
        self.commit("- [ ] **A new title.** Body.\n")
        (self.root / "brain/new.md").write_text("# New\n- [ ] **D1 — Stable within a note.** Body.\n")
        self.commit()
        payload = self.build()
        self.assertEqual(payload["summary"]["removed"], 2)
        self.assertEqual(payload["summary"]["open"], 2)
        self.assertEqual(payload["summary"]["events"], 6)
        self.assertFalse(any("renamed_from" in task for task in payload["tasks"]))

    def test_uncommitted_status_addition_and_removal_have_no_invented_commit_date(self):
        self.commit("- [ ] **D1 — Existing.**\n- [ ] **D2 — Will disappear.**\n")
        self.note.write_text("# Brain\n- [x] **D1 — Existing.**\n- [ ] **D3 — Uncommitted.**\n")
        payload = self.build()
        tasks = {task["code"]: task for task in payload["tasks"]}
        self.assertIsNone(payload["source_commit"])
        self.assertTrue(payload["working_tree_changes"])
        self.assertTrue(all(task["pending_change"] for task in tasks.values()))
        self.assertEqual(tasks["D1"]["status"], "completed")
        self.assertEqual(tasks["D1"]["last_transition"]["kind"], "first_recorded")
        self.assertEqual(tasks["D2"]["status"], "removed")
        self.assertIsNone(tasks["D3"]["first_recorded"])
        self.assertEqual(payload["summary"]["events"], 2)

    def test_parser_matches_brain_scope_and_rejects_ambiguous_identities(self):
        tasks = parse_tasks({"brain/index.md": b"---\nexample: '- [x] metadata'\n---\n# Brain\n"
                             b"```markdown\n- [ ] **D9 - Sample.**\n```\n- [X] **D1 - Real.**\n"})
        self.assertEqual(list(tasks), ["brain/index.md#D1"])
        self.assertTrue(tasks["brain/index.md#D1"]["done"])
        for content in (b"- [ ] **D1 - One.**\n- [x] **D1 - Two.**\n",
                        b"- [ ] **Same title.** Body.\n- [x] ** SAME   TITLE. ** Other body.\n"):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "duplicate task identity"):
                parse_tasks({"brain/index.md": content})

    def test_first_parent_merge_records_only_observed_mainline_states(self):
        root_commit = self.commit("")
        self.git("checkout", "-q", "-b", "study")
        self.commit("- [ ] **D1 — Branch study.**\n")
        self.commit("- [x] **D1 — Branch study.**\n")
        self.git("checkout", "-q", "main")
        self.commit()
        self.git("merge", "--no-ff", "--no-edit", "study",
                 env={"GIT_AUTHOR_DATE": "2026-09-11T18:00:00Z", "GIT_COMMITTER_DATE": "2026-09-11T18:00:00Z"})
        payload = self.build()
        self.assertEqual(payload["history"]["root_commit"], root_commit)
        self.assertEqual(payload["history"]["commit_count"], 3)
        self.assertEqual([event["kind"] for event in payload["events"]], ["first_recorded"])
        self.assertEqual(payload["events"][0]["status"], "completed")

    def test_missing_shallow_grafted_and_nested_git_history_are_rejected(self):
        commit = self.commit("- [ ] **D1 — Task.**\n")
        (self.root / ".git/shallow").write_text(commit + "\n")
        with self.assertRaisesRegex(ValueError, "Shallow"):
            build_activity(self.root)
        (self.root / ".git/shallow").unlink()
        (self.root / ".git/info/grafts").write_text(commit + "\n")
        with self.assertRaisesRegex(ValueError, "Grafted"):
            build_activity(self.root)
        (self.root / ".git/info/grafts").unlink()
        (self.root / "nested/brain").mkdir(parents=True)
        (self.root / "nested/brain/index.md").write_text("# Brain\n")
        with self.assertRaisesRegex(ValueError, "repository root"):
            build_activity(self.root / "nested")
        shutil.rmtree(self.root / ".git")
        with self.assertRaisesRegex(ValueError, "Git history"):
            build_activity(self.root)

    def test_archive_validation_rejects_stale_sources_states_and_missing_metadata(self):
        self.commit("- [ ] **D1 — Task.**\n")
        payload = self.build()
        shutil.rmtree(self.root / ".git")
        self.assertEqual(validate_snapshot(self.root, payload), payload)
        for mutate, message in (
            (lambda data: data.pop("history"), "history metadata"),
            (lambda data: data["history"].update(complete=False), "history metadata"),
            (lambda data: data["tasks"][0].update(done=True), "checkbox"),
            (lambda data: data["tasks"][0]["events"][0].update(kind="closed"), "lifecycle transition"),
            (lambda data: data["events"].clear(), "does not reconcile"),
            (lambda data: data["summary"].update(open=99), "totals"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                corrupted = copy.deepcopy(payload)
                mutate(corrupted)
                validate_snapshot(self.root, corrupted)
        self.note.write_text(self.note.read_text() + "\nDocumentation changed.\n")
        with self.assertRaisesRegex(ValueError, "stale"):
            validate_snapshot(self.root, payload)

    def test_deterministic_cli_check_accepts_artifact_commit_and_detects_roundtrip_transition(self):
        self.commit("- [ ] **D1 — Task.**\n")
        self.assertEqual(self.build(), self.build())
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        original = (self.root / "activity.json").read_bytes()
        self.assertEqual(self.cli().returncode, 0)
        self.assertEqual((self.root / "activity.json").read_bytes(), original)
        self.commit()  # The generated artifact itself cannot contain its own Git hash.
        checked = self.cli("--check")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.commit("- [x] **D1 — Task.**\n")
        self.commit("- [ ] **D1 — Task.**\n")
        self.assertEqual(self.cli("--validate-only").returncode, 0)
        result = self.cli("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("differs from Git history", result.stderr)

    def test_archive_cli_and_output_symlink_guard(self):
        self.commit("- [ ] **D1 — Task.**\n")
        self.assertEqual(self.cli().returncode, 0)
        shutil.rmtree(self.root / ".git")
        self.assertEqual(self.cli("--validate-only").returncode, 0)
        self.assertNotEqual(self.cli().returncode, 0)
        self.git("init", "-q", "-b", "main")
        self.commit()
        original = self.note.read_bytes()
        (self.root / "activity.json").unlink()
        (self.root / "activity.json").symlink_to(self.note)
        self.assertNotEqual(self.cli().returncode, 0)
        self.assertEqual(self.note.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
