import json
from pathlib import Path
import tempfile
import unittest

from brain_export import build_brain


class BrainExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'brain/research').mkdir(parents=True)
        (self.root / 'brain/index.md').write_text('# Brain\n[Research](research/index.md)\n')
        (self.root / 'brain/research/index.md').write_text('# Research\nParent: [Brain](../index.md)\n[Experiment](experiment.md)\n')
        (self.root / 'brain/research/experiment.md').write_text(
            '---\nsummary: Measurable experiment\nstatus: draft\nreviewed: 2026-09-11\n---\n'
            '# Experiment\nParent: [Research](index.md)\n- [ ] Open question\n- [x] Known result\n'
            '```markdown\n- [ ] Template only\n[Missing](does-not-exist.md)\n```\n'
            '[Paper](https://example.org/paper)\n[Script](../../script.py)\n')
        (self.root / 'script.py').write_text('# Source code\n')

    def test_graph_and_todos_exclude_fenced_examples_and_keep_sources(self):
        result = build_brain(self.root)
        note = next(node for node in result['nodes'] if node['title'] == 'Experiment')
        self.assertEqual(note['branch'], 'research')
        self.assertEqual(note['summary'], 'Measurable experiment')
        self.assertEqual(note['todos'], [{'text': 'Open question', 'done': False}, {'text': 'Known result', 'done': True}])
        self.assertEqual(note['links'], ['brain/research/index.md'])
        self.assertEqual(len(note['sources']), 2)
        self.assertEqual(len([edge for edge in result['edges'] if edge['kind'] == 'hierarchy']), 2)
        json.dumps(result, allow_nan=False)

    def test_missing_source_and_unreachable_note_fail_build(self):
        (self.root / 'script.py').unlink()
        with self.assertRaisesRegex(ValueError, 'Unresolved repository link'):
            build_brain(self.root)
        (self.root / 'script.py').write_text('')
        (self.root / 'brain/research/orphan.md').write_text('# Orphan\n')
        with self.assertRaisesRegex(ValueError, 'not reachable'):
            build_brain(self.root)

    def test_content_fingerprint_changes_when_notes_change(self):
        before = build_brain(self.root)
        path = self.root / 'brain/research/experiment.md'
        path.write_text(path.read_text() + '\nA negative result.\n')
        after = build_brain(self.root)
        self.assertNotEqual(before['content_sha256'], after['content_sha256'])

    def test_release_revision_is_recorded(self):
        (self.root / 'REVISION').write_text('a' * 40 + '\n')
        self.assertEqual(build_brain(self.root)['source_commit'], 'a' * 40)

    def test_symlink_cannot_export_an_external_file(self):
        (self.root / 'brain/research/external.md').symlink_to(self.root / 'script.py')
        with self.assertRaisesRegex(ValueError, 'inside its directory'):
            build_brain(self.root)


if __name__ == '__main__':
    unittest.main()
