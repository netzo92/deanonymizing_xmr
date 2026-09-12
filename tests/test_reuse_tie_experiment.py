"""Method checks use synthetic rings; frozen-input checks never fit a model."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from research import reuse_tie_experiment as rt


def ring(n, height=1, members=None, amount='9007199254740993', tx=None):
    members = members or [n * 2, n * 2 + 1]
    outputs = [{'amount': amount, 'index': str(i)} for i in members]
    return {'key_image': f'{n:064x}', 'tx_hash': f'{tx or n:064x}', 'height': height,
            'original_members': outputs, 'deterministic_output': outputs[-1],
            'reuse_counts': [1] * len(outputs),
            'candidates': [{**o, 'features': [i/(len(outputs)-1), 7.]} for i,o in enumerate(outputs)]}


class ReuseTieTests(unittest.TestCase):
    def test_midranks_remove_only_within_tie_order(self):
        self.assertEqual(rt.reuse_ranks([1,1,1], 'index_ordered'), [0., .5, 1.])
        self.assertEqual(rt.reuse_ranks([1,1,1], 'equal_midrank'), [.5, .5, .5])
        self.assertEqual(rt.reuse_ranks([2,1,2,9], 'equal_midrank'), [.5, 0., .5, 1.])
        for counts in ([3,1,2], [1,9], [9,5,3,1]):
            self.assertEqual(rt.reuse_ranks(counts, 'index_ordered'), rt.reuse_ranks(counts, 'equal_midrank'))
        for counts in ([1], [1,0], [1,True], [1,1.]):
            with self.assertRaises(ValueError): rt.reuse_ranks(counts, 'equal_midrank')

    def test_vectors_change_one_column_without_mutating_saved_input(self):
        records = [ring(1)]; before = copy.deepcopy(records)
        self.assertEqual(rt.vectors(records, ['reuse_rank_in_ring','other'], 'equal_midrank'), [[.5,7.],[.5,7.]])
        self.assertEqual(records, before)
        records[0]['candidates'][0]['features'][0] = .2
        with self.assertRaisesRegex(ValueError, 'Frozen reuse rank'):
            rt.vectors(records, ['reuse_rank_in_ring','other'], 'equal_midrank')

    def test_graph_includes_past_unlabeled_bridges_but_excludes_boundary_and_future(self):
        first, second = ring(1, 1, [1,2]), ring(2, 2, [8,9])
        bridge = ring(3, 3, [2,8]); bridge['deterministic_output'] = None
        outsider = ring(4, 1, [20,21])
        at_boundary = ring(5, 10, [9,20])
        roots, _, _, diagnostics = rt.past_components([first,second,bridge,outsider,at_boundary], 10)
        self.assertEqual(roots[first['key_image']], roots[second['key_image']])
        self.assertNotEqual(roots[first['key_image']], roots[outsider['key_image']])
        self.assertNotIn(at_boundary['key_image'], roots)
        self.assertEqual(diagnostics['components'], 2)
        self.assertEqual(diagnostics['excluded_at_or_after_cutoff_rings'], 1)
        changed_labels = copy.deepcopy([first,second,bridge,outsider,at_boundary])
        for r in changed_labels: r['deterministic_output'] = {'amount':'1','index':'999'}
        self.assertEqual(rt.past_components(changed_labels, 10), rt.past_components([first,second,bridge,outsider,at_boundary], 10))

    def test_graph_uses_exact_pair_identity_same_transaction_and_original_members(self):
        a = ring(1, members=[1,2]); b = ring(2, members=[1,2], amount='9007199254740994')
        c = ring(3, members=list(range(300, 801)), tx=1)
        roots, _, _, d = rt.past_components([a,b,c], 10)
        self.assertNotEqual(roots[a['key_image']], roots[b['key_image']])
        self.assertEqual(roots[a['key_image']], roots[c['key_image']])
        self.assertEqual(d['original_memberships'], 505)
        with self.assertRaisesRegex(ValueError, 'height'): rt.past_components([{**a, 'height':None}], 10)
        with self.assertRaisesRegex(ValueError, 'Duplicate'): rt.past_components([a,a], 10)

    def test_chronological_boundary_block_kept_whole_and_quarantine_is_transitive(self):
        labeled = [ring(n, n) for n in range(1, 11)]
        labeled[7]['height'] = 9  # boundary block contains rings8 and9
        bridge = ring(11, 2, [labeled[0]['original_members'][0]['index'], labeled[8]['original_members'][0]['index']])
        bridge['deterministic_output'] = None
        # ring2 connects to ring1 through their transaction rather than outputs.
        labeled[1]['tx_hash'] = labeled[0]['tx_hash']
        splits, graph = rt.comparison_splits({'records': labeled+[bridge]}, labeled)
        chronological, purged = splits['chronological'], splits['chronological_purged']
        self.assertEqual(chronological['cutoff_height'], 9)
        self.assertEqual([r['height'] for r in chronological['test']], [9,9,10])
        self.assertEqual(chronological['test'], purged['test'])
        self.assertEqual(set(graph['removed_training_ids']), {labeled[0]['key_image'],labeled[1]['key_image']})
        self.assertEqual(purged['direct_overlap']['shared_outputs'], 0)
        self.assertFalse(purged['minimum_fit_eligible'])

    def test_registration_rejects_code_and_protocol_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root/'research').mkdir()
            for name in (rt.RUNNER, rt.HELPER): (root/name).write_text('fixed\n')
            (root/rt.PROTOCOL).write_text('{"registered":true}\n')
            subprocess.run(['git','init','-q'], cwd=root, check=True)
            subprocess.run(['git','add','.'], cwd=root, check=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','Register'], cwd=root, check=True)
            commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root).decode().strip()
            with patch.object(rt, 'protocol_document', return_value={'registered': True}):
                self.assertEqual(rt.verify_registration(commit, root), {'registered':True})
                (root/rt.HELPER).write_text('changed\n')
                with self.assertRaisesRegex(ValueError, 'changed after'): rt.verify_registration(commit, root)
            with self.assertRaises(ValueError): rt.verify_registration('HEAD', root)

    def test_frozen_protocol_reconciles_graph_and_same_test_cohort_without_fitting(self):
        p = rt.protocol_document()
        rows = p['cohorts']
        self.assertEqual((rows['random']['manifest']['train']['rings'], rows['random']['manifest']['test']['rings']), (648,162))
        self.assertEqual(rows['chronological']['manifest']['test'], rows['chronological_purged']['manifest']['test'])
        self.assertEqual(rows['chronological']['manifest']['train']['rings'], 645)
        self.assertEqual(rows['chronological_purged']['manifest']['train']['rings'], 476)
        self.assertEqual(rows['chronological_purged']['train_removed'], 169)
        self.assertEqual(rows['chronological']['cutoff_height'], 55068)
        self.assertEqual(rows['chronological_purged']['direct_overlap']['shared_outputs'], 0)
        self.assertEqual(rows['chronological_purged']['direct_overlap']['test_rings_sharing_transaction'], 0)
        self.assertEqual(p['graph_diagnostics']['past_sampled_rings'], 1316)
        self.assertEqual(p['graph_diagnostics']['components'], 561)
        self.assertEqual(p['graph_diagnostics']['largest_component_rings'], 209)
        self.assertTrue(all(r['minimum_fit_eligible'] for r in rows.values()))


if __name__ == '__main__': unittest.main()
