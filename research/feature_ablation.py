"""FA2: preregistered, fixed-split ablation on the frozen EA1 matrix only.

No database, RPC, scorer import, deployment, or production-model mutation. Run
requires a Git commit containing the exact protocol and runner before fitting.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
MATRIX = 'research/results/feature_variation_matrix_2026-09-11.json.gz'
AUDIT = 'research/results/feature_variation_2026-09-11.json'
RUNNER = 'research/feature_ablation.py'
PROTOCOL = 'research/feature_ablation_protocol.json'
MATRIX_SHA = 'c13d80bfc3e8b35565608c18d9659925de2cbb0a61a3ec79e8bf745009a71568'
AUDIT_SHA = 'b450be6b96aa91d0744f36cd54a2401e3158e97e8ab3b4220aa032eed44db7bc'
SCORER_SHA = '1505277b4a79879fdcc7d1dc42e37075911716f7fa66ffd8eacd1389b78e998b'
VARIANTS = ['all_features', 'drop_constants', 'drop_constants_and_duplicates']
MODEL = dict(n_estimators=500, criterion='gini', max_depth=None, min_samples_split=2,
             min_samples_leaf=3, min_weight_fraction_leaf=0.0, max_features='sqrt',
             max_leaf_nodes=None, min_impurity_decrease=0.0, bootstrap=True,
             oob_score=False, n_jobs=1, random_state=42, verbose=0, warm_start=False,
             class_weight='balanced', ccp_alpha=0.0, max_samples=None, monotonic_cst=None)
LIMITATIONS = [
    'Retrospective agreement with selective stored deterministic labels under analyzer assumptions; no independently validated real-spend labels.',
    'The balanced EA1 sample is not weighted to the source population: 810 selected labeled legacy rings from a 12,076-ring eligible frame.',
    'Features were computed using the complete frozen graph at heights 0–58,900. Historical label eligibility and then-available graph context are unknown.',
    'Ring separation does not remove shared-output, shared-transaction or wider graph dependence. Overlap counts are diagnostics, not evidence of independence.',
    'All sampled amounts are nonzero, pre-RingCT historical data. No modern, unresolved-ring, forward-time, calibration or deployed-performance claim follows.',
    'One fixed split and one seed per variant; no hyperparameter selection, significance tests, IID confidence intervals or population-generalization claim.',
    'Each variant runs once in a fresh process. Elapsed time and whole-process peak RSS include operational noise and do not establish a capacity or speedup benchmark.',
    'Removing columns changes random-forest sqrt feature subsampling and tree search even when columns are constant or exact duplicates.',
]

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def reject_constant(value):
    raise ValueError(f'Nonfinite JSON constant {value}')


def json_bytes(raw):
    return json.loads(raw, parse_constant=reject_constant)


def output_key(value):
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not re.fullmatch(r'0|[1-9][0-9]*', value[k]) for k in ('amount', 'index')):
        raise ValueError('Output identities must be exact nonnegative decimal strings')
    return int(value['amount']), int(value['index'])


def eligible_records(matrix):
    names = matrix.get('feature_names')
    if matrix.get('schema_version') != 1 or not isinstance(names, list) or len(names) != len(set(names)) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError('Invalid feature schema')
    records = matrix.get('records')
    if not isinstance(records, list) or len(records) > 2500:
        raise ValueError('Invalid or oversized frozen ring sample')
    selected, seen = [], set()
    for record in records:
        key = record.get('key_image')
        if not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key) or key in seen:
            raise ValueError('Duplicate or malformed key image')
        seen.add(key)
        if record.get('status') != 'deterministic_training':
            continue
        candidates = record.get('candidates', [])
        if not 2 <= len(candidates) <= 128 or record.get('original_ring_size') != len(candidates) or record.get('feature_ring_size') != len(candidates):
            raise ValueError('Eligible records require complete original non-singleton rings')
        identities = [output_key(candidate) for candidate in candidates]
        if identities != sorted(set(identities)) or set(identities) != {output_key(member) for member in record.get('original_members', [])}:
            raise ValueError('Original and feature candidates must agree in canonical order')
        if len({amount for amount, _ in identities}) != 1 or identities[0][0] == 0:
            raise ValueError('The registered sample contains only single-amount nonzero rings')
        if output_key(record.get('deterministic_output')) not in identities:
            raise ValueError('Stored deterministic label is not in its original ring')
        if not isinstance(record.get('height'), int) or not 0 <= record['height'] <= 58900:
            raise ValueError('Unexpected historical height')
        reuse = record.get('reuse_counts')
        if not isinstance(reuse, list) or len(reuse) != len(candidates) or any(type(value) is not int or value < 1 for value in reuse):
            raise ValueError('Missing exact full-graph reuse counts')
        for candidate in candidates:
            values = candidate.get('features')
            if not isinstance(values, list) or len(values) != len(names) or any(type(value) not in (float, int) or not math.isfinite(value) for value in values):
                raise ValueError('Feature vectors must be complete finite numeric rows')
        selected.append(record)
    return sorted(selected, key=lambda item: item['key_image'])


def load_inputs(root=ROOT):
    matrix_raw, audit_raw = (root / MATRIX).read_bytes(), (root / AUDIT).read_bytes()
    if len(matrix_raw) > 4_000_000 or digest(matrix_raw) != MATRIX_SHA or digest(audit_raw) != AUDIT_SHA:
        raise ValueError('Frozen EA1 input hash mismatch')
    with gzip.GzipFile(fileobj=__import__('io').BytesIO(matrix_raw)) as stream:
        expanded = stream.read(10_000_001)
    if len(expanded) > 10_000_000:
        raise ValueError('Expanded matrix exceeds bound')
    matrix, audit = json_bytes(expanded), json_bytes(audit_raw)
    if audit['matrix']['sha256'] != MATRIX_SHA or audit['matrix']['uncompressed_sha256'] != digest(expanded) or matrix['membership_sha256'] != audit['sampling']['membership_sha256']:
        raise ValueError('EA1 matrix and audit provenance disagree')
    records = eligible_records(matrix)
    if len(matrix['records']) != 2000 or len(records) != 810 or sum(len(item['candidates']) for item in records) != 4533 or len(matrix['feature_names']) != 24:
        raise ValueError('Frozen eligibility denominators differ from registration')
    return matrix, audit, records


def fixed_split(records):
    import numpy as np
    ordered = sorted(records, key=lambda item: item['key_image'])
    indices = np.random.RandomState(42).permutation(len(ordered))
    boundary = int(len(ordered) * 0.8)
    return [ordered[int(i)] for i in indices[:boundary]], [ordered[int(i)] for i in indices[boundary:]]


def split_manifest(train, test):
    return {name: {'key_images': [item['key_image'] for item in records],
                   'identity_sha256': digest(canonical([item['key_image'] for item in records])),
                   'rings': len(records), 'candidates': sum(len(item['candidates']) for item in records)}
            for name, records in [('train', train), ('test', test)]}


def feature_masks(train_rows, names):
    import numpy as np
    values = np.asarray(train_rows, dtype=float)
    if values.ndim != 2 or not values.shape[0] or values.shape[1] != len(names) or not np.isfinite(values).all():
        raise ValueError('Mask discovery requires finite training rows')
    constants = [i for i in range(len(names)) if np.all(values[:, i] == values[0, i])]
    variable = [i for i in range(len(names)) if i not in constants]
    kept, duplicates = [], []
    for index in variable:
        earlier = next((other for other in kept if np.array_equal(values[:, index], values[:, other])), None)
        if earlier is None:
            kept.append(index)
        else:
            duplicates.append({'removed': names[index], 'retained': names[earlier]})
    if not kept:
        raise ValueError('No variable feature remains after the registered masks')
    return {'all_features': list(range(len(names))), 'drop_constants': variable,
            'drop_constants_and_duplicates': kept, 'constant_features': [names[i] for i in constants],
            'duplicate_representatives': duplicates}


def overlap_diagnostics(train, test):
    outputs = lambda records: {output_key(candidate) for record in records for candidate in record['candidates']}
    a, b = outputs(train), outputs(test)
    train_tx = {item.get('tx_hash') for item in train}
    shared = a & b
    return {'train_unique_outputs': len(a), 'test_unique_outputs': len(b), 'shared_outputs': len(shared),
            'test_rings_with_shared_output': sum(any(output_key(candidate) in a for candidate in record['candidates']) for record in test),
            'test_rings_sharing_transaction': sum(record.get('tx_hash') in train_tx for record in test),
            'test_labeled_outputs_also_in_train': sum(output_key(record['deterministic_output']) in a for record in test),
            'shared_output_identity_sha256': digest(canonical([{'amount': str(a), 'index': str(i)} for a, i in sorted(shared)])),
            'interpretation': 'Direct overlap only; absence of direct overlap does not establish independence or remove snapshot-derived leakage.'}


def protocol_document(root=ROOT):
    matrix, audit, records = load_inputs(root)
    train, test = fixed_split(records)
    return {'schema_version': 1, 'experiment_id': 'FA2', 'phase': 'registered_before_model_fitting',
            'question': 'On this fixed retrospective sample and split, what changes when train-constant and exact duplicate columns are removed from the current random forest?',
            'inputs': {MATRIX: MATRIX_SHA, AUDIT: AUDIT_SHA},
            'runner_sha256': digest((root / RUNNER).read_bytes()), 'reference_scorer_sha256': SCORER_SHA,
            'feature_version': matrix['feature_version'], 'feature_names': matrix['feature_names'],
            'eligible_rings': 810, 'eligible_candidates': 4533,
            'runtime_requirements': {'python': platform.python_version(), **{key: importlib.metadata.version(key) for key in ('numpy', 'scikit-learn', 'scipy')}},
            'eligibility': 'Only deterministic_training records with complete original rings, exactly one stored label in the candidates, finite vectors and nonzero amount. All unresolved-scoring records excluded.',
            'split_rule': 'Sort key_image lexicographically, then NumPy RandomState(42).permutation. First int(N*0.8) rings train, remainder test; preserve permutation order. No stratification or resampling.',
            'split': split_manifest(train, test), 'variants': VARIANTS, 'model': MODEL,
            'scaling': 'StandardScaler(with_mean=True, with_std=True) fitted on training candidates separately for each mask. No all-data refit.',
            'mask_rule': 'Discover on raw training candidates only. Remove columns exactly equal to their first training value, then exact np.array_equal duplicate columns, retaining the earliest original feature index. Never inspect heldout values to choose a mask.',
            'tie_rule': 'Model operational prediction is first exact maximum probability in numeric (amount,index) candidate order, matching argmax. Also record top ties and fractional expected tie agreement. Descriptive min-reuse ties receive uniform fractional credit.',
            'analysis': ['Same 162 heldout rings for all variants; record operational label agreement, every candidate score and tie-aware expected agreement.',
                         'Report paired both-correct/baseline-only/variant-only/neither counts, changed predictions and exact net difference against all_features.',
                         'Report uniform, highest output index and minimum full-snapshot reuse descriptive expected agreement on the same test rings.',
                         'Report split overlap and ring-size cohorts. No p-values, independence-assuming intervals, hyperparameter search or post-hoc model selection.'],
            'resources': {'variant_order': VARIANTS, 'jobs': 1, 'fresh_process_per_variant': True, 'worker_timeout_seconds': 180,
                          'peak_memory': 'resource.getrusage(RUSAGE_SELF).ru_maxrss in fresh worker; macOS bytes, Linux KiB converted to bytes.',
                          'runtime': 'Separate scaler-fit, forest-fit and test-score wall times; also full worker process wall time.'},
            'expected_outputs': ['summary.json', 'candidate_scores.json.gz', 'all_features.json', 'drop_constants.json', 'drop_constants_and_duplicates.json'],
            'limitations': LIMITATIONS}


def verify_registration(commit, root=ROOT):
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('An exact preregistration commit is required')
    for path in (RUNNER, PROTOCOL):
        try:
            registered = subprocess.run(['git', '--no-replace-objects', 'show', f'{commit}:{path}'], cwd=root,
                                        check=True, capture_output=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError('Preregistration commit does not contain the protocol and runner') from error
        if registered != (root / path).read_bytes():
            raise ValueError(f'{path} differs from preregistration; stop before fitting')
    protocol = json_bytes((root / PROTOCOL).read_bytes())
    if protocol != protocol_document(root):
        raise ValueError('Protocol or frozen split differs from the registered design')
    return protocol


def ring_outcome(record, probabilities):
    if len(probabilities) != len(record['candidates']) or any(not math.isfinite(float(p)) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError('Invalid candidate scores')
    identities = [output_key(item) for item in record['candidates']]
    maximum = max(probabilities)
    top = [i for i, value in enumerate(probabilities) if value == maximum]
    label_index = identities.index(output_key(record['deterministic_output']))
    selected = top[0]
    credit = Fraction(int(label_index in top), len(top))
    return {'key_image': record['key_image'], 'ring_size': len(identities), 'stratum': record['stratum'],
            'height': record['height'], 'deterministic_output': record['deterministic_output'],
            'selected_output': {k: record['candidates'][selected][k] for k in ('amount', 'index')},
            'correct': selected == label_index, 'top_ties': len(top), 'expected_tie_credit': float(credit),
            'expected_tie_credit_exact': {'numerator': credit.numerator, 'denominator': credit.denominator},
            'candidates': [{**{key: candidate[key] for key in ('amount', 'index')}, 'score': float(score), 'stored_label': i == label_index}
                           for i, (candidate, score) in enumerate(zip(record['candidates'], probabilities))]}


def summarize_outcomes(outcomes):
    total = len(outcomes)
    if not total:
        raise ValueError('No heldout rings')
    credit = sum((Fraction(row['expected_tie_credit_exact']['numerator'], row['expected_tie_credit_exact']['denominator']) for row in outcomes), Fraction())
    return {'rings': total, 'candidates': sum(row['ring_size'] for row in outcomes),
            'correct': sum(row['correct'] for row in outcomes), 'agreement': sum(row['correct'] for row in outcomes) / total,
            'top_tied_rings': sum(row['top_ties'] > 1 for row in outcomes), 'expected_tie_correct': float(credit),
            'expected_tie_correct_exact': {'numerator': credit.numerator, 'denominator': credit.denominator},
            'expected_tie_agreement': float(credit / total)}


def paired_outcomes(baseline, variant):
    if [row['key_image'] for row in baseline] != [row['key_image'] for row in variant] or any(left['deterministic_output'] != right['deterministic_output'] for left, right in zip(baseline, variant)):
        raise ValueError('Paired results must use identical ordered test rings and labels')
    counts = Counter((left['correct'], right['correct']) for left, right in zip(baseline, variant))
    return {'rings': len(baseline), 'both_correct': counts[True, True], 'baseline_only_correct': counts[True, False],
            'variant_only_correct': counts[False, True], 'neither_correct': counts[False, False],
            'net_correct_change': counts[False, True] - counts[True, False],
            'agreement_change': (counts[False, True] - counts[True, False]) / len(baseline),
            'changed_selected_outputs': sum(left['selected_output'] != right['selected_output'] for left, right in zip(baseline, variant))}


def comparators(test):
    outputs = {}
    for name in ('uniform', 'highest_index', 'minimum_reuse'):
        scores = []
        for record in test:
            count = len(record['candidates'])
            if name == 'uniform': probabilities = [1 / count] * count
            elif name == 'highest_index':
                top = max(int(item['index']) for item in record['candidates'])
                probabilities = [float(int(item['index']) == top) for item in record['candidates']]
            else:
                lowest = min(record['reuse_counts']); probabilities = [float(value == lowest) for value in record['reuse_counts']]
            scores.append(ring_outcome(record, probabilities))
        summary = summarize_outcomes(scores)
        outputs[name] = {'rings': summary['rings'], 'expected_correct': summary['expected_tie_correct'],
                         'expected_correct_exact': summary['expected_tie_correct_exact'], 'expected_agreement': summary['expected_tie_agreement'],
                         'tied_rings': summary['top_tied_rings']}
    return outputs


def worker(variant, destination, registered_commit, root=ROOT):
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    protocol = verify_registration(registered_commit, root)
    matrix, _, records = load_inputs(root)
    train, test = fixed_split(records)
    raw_train = [candidate['features'] for record in train for candidate in record['candidates']]
    masks = feature_masks(raw_train, matrix['feature_names'])
    indices = masks[variant]
    labels = np.asarray([int(output_key(candidate) == output_key(record['deterministic_output'])) for record in train for candidate in record['candidates']])
    if set(labels.tolist()) != {0, 1}: raise ValueError('Training requires both classes')
    values = np.asarray(raw_train)[:, indices]
    start = time.perf_counter(); scaler = StandardScaler(); scaled = scaler.fit_transform(values); scale_seconds = time.perf_counter() - start
    model = RandomForestClassifier(**protocol['model'])
    start = time.perf_counter(); model.fit(scaled, labels); fit_seconds = time.perf_counter() - start
    if model.classes_.tolist() != [0, 1]: raise ValueError('Unexpected model class ordering')
    start = time.perf_counter()
    scores = model.predict_proba(scaler.transform(np.asarray([candidate['features'] for record in test for candidate in record['candidates']])[:, indices]))[:, 1]
    score_seconds = time.perf_counter() - start
    outcomes, offset = [], 0
    for record in test:
        n = len(record['candidates']); outcomes.append(ring_outcome(record, scores[offset:offset+n].tolist())); offset += n
    cohorts = {name: summarize_outcomes([row for row in outcomes if row['stratum'] == name]) for name in sorted({row['stratum'] for row in outcomes})}
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    data = {'variant': variant, 'feature_indices': indices, 'feature_names': [matrix['feature_names'][i] for i in indices],
            'masks_detected_on_train': masks, 'summary': summarize_outcomes(outcomes), 'cohorts': cohorts,
            'resources': {'scaler_fit_seconds': scale_seconds, 'forest_fit_seconds': fit_seconds, 'test_score_seconds': score_seconds, 'process_peak_rss_bytes': peak},
            'scaler': {'mean': scaler.mean_.tolist(), 'scale': scaler.scale_.tolist(), 'variance': scaler.var_.tolist()},
            'feature_importances': model.feature_importances_.tolist(), 'effective_model_parameters': model.get_params(), 'outcomes': outcomes}
    Path(destination).write_bytes(canonical(data) + b'\n')


def run(registered_commit, output_dir, root=ROOT):
    protocol = verify_registration(registered_commit, root)
    target = Path(output_dir).resolve()
    if target.exists(): raise ValueError('Output directory already exists; preserve previous results')
    if target == root or not target.parent.is_dir(): raise ValueError('Output parent must already exist')
    started = datetime.now(timezone.utc).isoformat(); began = time.perf_counter()
    matrix, audit, records = load_inputs(root); train, test = fixed_split(records)
    with tempfile.TemporaryDirectory(prefix='.fa2-', dir=target.parent) as temporary:
        staging = Path(temporary); models = {}
        for variant in VARIANTS:
            output = staging / f'{variant}.json'; start = time.perf_counter()
            env = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
            subprocess.run([sys.executable, str(root / RUNNER), '_worker', '--variant', variant, '--output', str(output), '--registered-commit', registered_commit],
                           cwd=root, env=env, check=True, timeout=180, capture_output=True)
            models[variant] = json_bytes(output.read_bytes()); models[variant]['resources']['worker_wall_seconds'] = time.perf_counter() - start
            output.write_bytes(canonical(models[variant]) + b'\n')
        paired = {variant: paired_outcomes(models[VARIANTS[0]]['outcomes'], models[variant]['outcomes']) for variant in VARIANTS[1:]}
        score_payload = {'schema_version': 1, 'experiment_id': 'FA2', 'registered_commit': registered_commit,
                         'score_semantics': 'uncalibrated candidate probability; agreement with selected stored deterministic labels',
                         'split': protocol['split'], 'variants': {key: value['outcomes'] for key, value in models.items()}}
        compressed = gzip.compress(canonical(score_payload), mtime=0); (staging / 'candidate_scores.json.gz').write_bytes(compressed)
        load_inputs(root); verify_registration(registered_commit, root)
        summary = {'schema_version': 1, 'experiment_id': 'FA2', 'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
                   'registered_commit': registered_commit, 'protocol_path': PROTOCOL, 'protocol_sha256': digest((root / PROTOCOL).read_bytes()),
                   'runner_sha256': digest((root / RUNNER).read_bytes()), 'inputs': protocol['inputs'],
                   'source_scope': audit['scope'], 'split': protocol['split'], 'versions': {'python': platform.python_version(), **{key: importlib.metadata.version(key) for key in ('numpy', 'scikit-learn', 'scipy')}},
                   'platform': platform.platform(), 'model_settings': MODEL, 'overlap': overlap_diagnostics(train, test),
                   'variants': {key: {field: value[field] for field in ('feature_indices', 'feature_names', 'summary', 'cohorts', 'resources', 'masks_detected_on_train')} for key, value in models.items()},
                   'paired_vs_all_features': paired, 'descriptive_comparators': comparators(test), 'elapsed_seconds': time.perf_counter() - began,
                   'artifacts': {path.name: {'sha256': digest(path.read_bytes()), 'bytes': path.stat().st_size} for path in sorted(staging.iterdir())},
                   'limitations': LIMITATIONS}
        (staging / 'summary.json').write_bytes(json.dumps(summary, indent=2, allow_nan=False).encode() + b'\n')
        staging.rename(target)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare'); prepare.add_argument('--output', type=Path, default=ROOT / PROTOCOL)
    execute = commands.add_parser('run'); execute.add_argument('--registered-commit', required=True); execute.add_argument('--output-dir', type=Path, required=True)
    internal = commands.add_parser('_worker'); internal.add_argument('--registered-commit', required=True); internal.add_argument('--variant', choices=VARIANTS, required=True); internal.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        data = protocol_document(); args.output.write_bytes(json.dumps(data, indent=2, allow_nan=False).encode() + b'\n'); print(f'Prepared protocol with {data["split"]["train"]["rings"]} train / {data["split"]["test"]["rings"]} test rings; no model fitted.')
    elif args.command == '_worker': worker(args.variant, args.output, args.registered_commit)
    else:
        data = run(args.registered_commit, args.output_dir); print(json.dumps({'output': str(args.output_dir), 'variants': {key: value['summary'] for key, value in data['variants'].items()}, 'paired': data['paired_vs_all_features']}))

if __name__ == '__main__':
    main()
