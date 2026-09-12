"""Synthetic correctness and preregistration checks; no database or real-data fit."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('feature_ablation', ROOT / 'research/feature_ablation.py')
fa = importlib.util.module_from_spec(spec); spec.loader.exec_module(fa)


def ring(number=1, amount='9007199254740993'):
    return {'key_image': f'{number:064x}', 'status': 'deterministic_training', 'height': 100,
            'tx_hash': f'{number:064x}', 'stratum': 'deterministic_training|legacy_nonzero|2',
            'feature_ring_size': 2, 'original_ring_size': 2, 'reuse_counts': [3, 1],
            'original_members': [{'amount': amount, 'index': '1'}, {'amount': amount, 'index': '2'}],
            'deterministic_output': {'amount': amount, 'index': '2'},
            'candidates': [{'amount': amount, 'index': '1', 'features': [0., 7., 0.]}, {'amount': amount, 'index': '2', 'features': [1., 7., 1.]}]}


class FeatureAblationTests(unittest.TestCase):
    def test_train_only_masks_ignore_holdout_only_variation(self):
        train = [[0, 8, 0, 4], [1, 8, 1, 4], [2, 8, 2, 4]]
        masks = fa.feature_masks(train, ['a', 'constant', 'duplicate_a', 'other_constant'])
        self.assertEqual(masks['drop_constants'], [0, 2])
        self.assertEqual(masks['drop_constants_and_duplicates'], [0])
        self.assertEqual(masks['duplicate_representatives'], [{'removed': 'duplicate_a', 'retained': 'a'}])
        # Holdout could break both a constant and the training duplicate, but it
        # is never supplied to the mask discovery function or used to select it.
        holdout = [[8, 99, 7, 4]]
        self.assertEqual(np.asarray(holdout)[:, masks['drop_constants_and_duplicates']].tolist(), [[8]])
        with self.assertRaises(ValueError): fa.feature_masks([[float('nan')]], ['bad'])

    def test_fixed_ring_split_is_order_invariant_and_disjoint(self):
        records = [ring(n) for n in range(1, 11)]
        train, test = fa.fixed_split(records)
        train2, test2 = fa.fixed_split(list(reversed(records)))
        self.assertEqual(fa.split_manifest(train, test), fa.split_manifest(train2, test2))
        self.assertEqual((len(train), len(test)), (8, 2))
        self.assertFalse({x['key_image'] for x in train} & {x['key_image'] for x in test})

    def test_eligibility_preserves_exact_amount_and_rejects_label_mismatch(self):
        matrix = {'schema_version': 1, 'feature_names': ['a','constant','duplicate'], 'records': [ring()]}
        self.assertEqual(fa.output_key(fa.eligible_records(matrix)[0]['deterministic_output']), (9007199254740993, 2))
        bad = copy.deepcopy(matrix); bad['records'][0]['deterministic_output']['amount'] = '9007199254740992'
        with self.assertRaisesRegex(ValueError, 'label'): fa.eligible_records(bad)
        bad = copy.deepcopy(matrix); bad['records'][0]['candidates'][0]['amount'] = 9007199254740993
        with self.assertRaisesRegex(ValueError, 'decimal strings'): fa.eligible_records(bad)
        unresolved = ring(2); unresolved['status'] = 'unresolved_scoring'; unresolved['deterministic_output'] = None
        self.assertEqual(len(fa.eligible_records({**matrix, 'records': [ring(), unresolved]})), 1)

    def test_invalid_candidate_memberships_nonfinite_or_duplicate_ring_rejected(self):
        matrix = {'schema_version': 1, 'feature_names': ['a','constant','duplicate'], 'records': [ring()]}
        bad = copy.deepcopy(matrix); bad['records'][0]['original_members'].pop()
        with self.assertRaisesRegex(ValueError, 'Original'): fa.eligible_records(bad)
        bad = copy.deepcopy(matrix); bad['records'][0]['candidates'][1]['features'][0] = float('inf')
        with self.assertRaisesRegex(ValueError, 'finite'): fa.eligible_records(bad)
        with self.assertRaisesRegex(ValueError, 'Duplicate'): fa.eligible_records({**matrix, 'records': [ring(), ring()]})

    def test_score_ties_match_first_argmax_and_preserve_fractional_credit(self):
        result = fa.ring_outcome(ring(), [.5, .5])
        self.assertEqual(result['selected_output']['index'], '1')
        self.assertFalse(result['correct'])
        self.assertEqual(result['expected_tie_credit_exact'], {'numerator': 1, 'denominator': 2})
        self.assertEqual([item['stored_label'] for item in result['candidates']], [False, True])
        with self.assertRaises(ValueError): fa.ring_outcome(ring(), [float('nan'), 1.])
        with self.assertRaises(ValueError): fa.ring_outcome(ring(), [1.])

    def test_paired_results_use_identical_labels_and_reconcile_changes(self):
        baseline = [fa.ring_outcome(ring(1), [.9,.1]), fa.ring_outcome(ring(2), [.1,.9])]
        variant = [fa.ring_outcome(ring(1), [.1,.9]), fa.ring_outcome(ring(2), [.9,.1])]
        result = fa.paired_outcomes(baseline, variant)
        self.assertEqual(result['variant_only_correct'], 1)
        self.assertEqual(result['baseline_only_correct'], 1)
        self.assertEqual(result['net_correct_change'], 0)
        self.assertEqual(result['changed_selected_outputs'], 2)
        with self.assertRaises(ValueError): fa.paired_outcomes(baseline, list(reversed(variant)))
        changed = copy.deepcopy(variant); changed[0]['deterministic_output']['index'] = '1'
        with self.assertRaises(ValueError): fa.paired_outcomes(baseline, changed)

    def test_descriptive_rules_use_fractional_ties(self):
        record = ring(); record['reuse_counts'] = [1,1]
        result = fa.comparators([record])
        self.assertEqual(result['uniform']['expected_agreement'], .5)
        self.assertEqual(result['minimum_reuse']['expected_agreement'], .5)
        self.assertEqual(result['highest_index']['expected_agreement'], 1.)

    def test_overlap_uses_amount_index_identity_and_keeps_dependence_visible(self):
        first, second, third = ring(1), ring(2), ring(3, amount='9007199254740994')
        result = fa.overlap_diagnostics([first], [second, third])
        self.assertEqual(result['shared_outputs'], 2)
        self.assertEqual(result['test_rings_with_shared_output'], 1)
        self.assertEqual(result['test_labeled_outputs_also_in_train'], 1)
        self.assertEqual(result['test_rings_sharing_transaction'], 0)

    def test_preregistration_rejects_uncommitted_edits_before_any_training(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'research').mkdir()
            (root/fa.RUNNER).write_text('original code\n'); (root/fa.PROTOCOL).write_text('{"registered": true}\n')
            subprocess.run(['git','init','-q'], cwd=root, check=True)
            subprocess.run(['git','add','.'], cwd=root, check=True)
            subprocess.run(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','Register'], cwd=root, check=True)
            commit = subprocess.check_output(['git','rev-parse','HEAD'], cwd=root).decode().strip()
            with patch.object(fa, 'protocol_document', return_value={'registered': True}):
                self.assertEqual(fa.verify_registration(commit, root), {'registered': True})
                (root/fa.RUNNER).write_text('changed after registration\n')
                with self.assertRaisesRegex(ValueError, 'differs from preregistration'): fa.verify_registration(commit, root)
            with self.assertRaises(ValueError): fa.verify_registration('HEAD', root)

    def test_registered_protocol_pins_real_eligibility_and_split_without_fitting(self):
        protocol = fa.protocol_document(ROOT)
        self.assertEqual(protocol['eligible_rings'], 810)
        self.assertEqual(protocol['eligible_candidates'], 4533)
        self.assertEqual(protocol['split']['train']['rings'], 648)
        self.assertEqual(protocol['split']['test']['rings'], 162)
        self.assertEqual(protocol['split']['train']['candidates'], 3643)
        self.assertEqual(protocol['split']['test']['candidates'], 890)
        self.assertEqual(protocol['model']['n_estimators'], 500)
        self.assertEqual(protocol['model']['min_samples_leaf'], 3)
        self.assertEqual(protocol['model']['class_weight'], 'balanced')
        self.assertEqual(protocol['model']['n_jobs'], 1)
        self.assertEqual(protocol['variants'], ['all_features','drop_constants','drop_constants_and_duplicates'])


if __name__ == '__main__': unittest.main()
