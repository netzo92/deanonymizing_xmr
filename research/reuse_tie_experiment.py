"""FA3: registered tie-policy sensitivity on the frozen EA1 matrix, offline.

Only the reuse-rank column changes. Time/graph partitions remain retrospective:
features and stored labels were obtained from the full original snapshot.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import importlib.metadata
import json
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
sys.path.insert(0, str(ROOT))
from research import feature_ablation as fa

RUNNER = 'research/reuse_tie_experiment.py'
PROTOCOL = 'research/reuse_tie_protocol.json'
HELPER = 'research/feature_ablation.py'
VARIANTS = ('index_ordered', 'equal_midrank')
COHORTS = ('random', 'chronological', 'chronological_purged')
LABELS = {'random': 'FA2 random ring split', 'chronological': 'Later blocks; earlier training',
          'chronological_purged': 'Same later blocks; connected training removed'}
LIMITATIONS = [
    'Retrospective agreement with selective stored deterministic labels, not independent spend truth or forward accuracy.',
    'All data are sampled legacy nonzero-amount rings from heights 0–58,900. The source-weighted population, modern eras and unresolved outcomes are not evaluated.',
    'The random split and its baseline were already inspected in FA2. This is a follow-up sensitivity experiment, not a fresh untouched test.',
    'Feature values still use the full original snapshot. Historical feature availability and label eligibility were not reconstructed; HE3 remains open.',
    'Graph components use only sampled memberships before the cutoff. Heldout test identities are used to quarantine overlapping training components: an offline evaluation filter, not an online grouping rule.',
    'The frozen matrix contains only 2,000 sampled rings. Missing rings and wider source-graph paths can connect apparently separate components; zero sampled overlap does not prove independence.',
    'The chronological and purged comparisons use the same test rings but different training sizes/populations. Their differences do not isolate a causal effect of overlap.',
    'Only reuse_rank_in_ring changes. Other reuse and index-position features remain; this cannot isolate all reuse or all index information.',
    'Retraining can change predictions even for test rings without reuse ties. These subgroups are descriptive diagnostics, not causal negative controls.',
    'One fixed model seed and one fit per variant/cohort, with no tuning, significance test or independence-assuming interval. Single timing/RSS observations do not establish repeatable efficiency gains.',
    'Candidate scores are uncalibrated model outputs. No result identifies sender, address or wallet ownership; the live scorer is not changed by this runner.',
]


def reuse_ranks(counts, variant):
    if variant not in VARIANTS or not 2 <= len(counts) <= 128 or any(type(v) is not int or v < 1 for v in counts):
        raise ValueError('Tie ranks require bounded positive integer reuse counts and a known variant')
    order = sorted(range(len(counts)), key=lambda i: counts[i])
    ranks = [0.] * len(counts)
    if variant == 'index_ordered':
        for rank, index in enumerate(order):
            ranks[index] = rank / (len(counts) - 1)
    else:
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and counts[order[end]] == counts[order[start]]:
                end += 1
            value = (start + end - 1) / (2 * (len(counts) - 1))
            for index in order[start:end]:
                ranks[index] = value
            start = end
    return ranks


def vectors(records, names, variant):
    column = names.index('reuse_rank_in_ring')
    result = []
    for record in records:
        original = reuse_ranks(record['reuse_counts'], 'index_ordered')
        changed = reuse_ranks(record['reuse_counts'], variant)
        for index, candidate in enumerate(record['candidates']):
            if candidate['features'][column] != original[index]:
                raise ValueError('Frozen reuse rank does not match the current index-order rule')
            row = list(candidate['features'])
            row[column] = changed[index]
            result.append(row)
    return result


def past_components(records, cutoff):
    """Build sample components without any at/after-cutoff ring edges."""
    if type(cutoff) is not int or cutoff < 1 or len(records) > 2500:
        raise ValueError('Invalid component boundary or oversized sample')
    if any(type(r.get('height')) is not int or not 0 <= r['height'] <= 58900 for r in records):
        raise ValueError('Missing or invalid sampled ring height')
    past = [r for r in records if r['height'] < cutoff]
    parents = {r['key_image']: r['key_image'] for r in past}
    if len(parents) != len(past):
        raise ValueError('Duplicate sampled ring identity')
    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[max(a, b)] = min(a, b)
    outputs, transactions = {}, {}
    edge_count = 0
    for record in past:
        key, tx = record['key_image'], record.get('tx_hash')
        if not isinstance(tx, str) or not re.fullmatch('[a-f0-9]{64}', tx):
            raise ValueError('Missing original transaction identity')
        members = record.get('original_members')
        # Unresolved sampled rings may have many eliminated original members;
        # the 128 modeled-candidate bound is not the graph membership bound.
        if not isinstance(members, list) or not 2 <= len(members) <= 1024:
            raise ValueError('Invalid original component memberships')
        identities = {fa.output_key(item) for item in members}
        if len(identities) != len(members):
            raise ValueError('Duplicate original output identity')
        for output in identities:
            if output in outputs:
                union(key, outputs[output])
            else:
                outputs[output] = key
        edge_count += len(identities)
        if tx in transactions:
            union(key, transactions[tx])
        else:
            transactions[tx] = key
    roots = {key: find(key) for key in parents}
    sizes = Counter(roots.values())
    return roots, {key: roots[value] for key, value in outputs.items()}, {key: roots[value] for key, value in transactions.items()}, {
        'past_sampled_rings': len(past), 'original_memberships': edge_count,
        'components': len(sizes), 'largest_component_rings': max(sizes.values(), default=0),
        'component_assignment_sha256': fa.digest(fa.canonical(roots)),
        'excluded_at_or_after_cutoff_rings': len(records) - len(past)}


def comparison_splits(matrix, eligible):
    if len(eligible) < 10:
        raise ValueError('At least ten eligible rings required')
    random_train, random_test = fa.fixed_split(eligible)
    chronological = sorted(eligible, key=lambda r: (r['height'], r['key_image']))
    cutoff = chronological[int(len(chronological) * .8)]['height']
    train = [r for r in chronological if r['height'] < cutoff]
    test = [r for r in chronological if r['height'] >= cutoff]
    if not train or not test:
        raise ValueError('Strict chronological split has an empty partition')
    roots, outputs, transactions, graph = past_components(matrix['records'], cutoff)
    quarantine = set()
    for record in test:
        quarantine.update(outputs[fa.output_key(item)] for item in record['original_members'] if fa.output_key(item) in outputs)
        if record['tx_hash'] in transactions:
            quarantine.add(transactions[record['tx_hash']])
    kept = [r for r in train if roots[r['key_image']] not in quarantine]
    removed = [r['key_image'] for r in train if roots[r['key_image']] in quarantine]
    graph.update(quarantined_components=len(quarantine), removed_training_rings=len(removed),
                 removed_training_fraction=len(removed) / len(train), removed_training_ids=removed)
    result = {}
    for name, tr, te, boundary in [('random', random_train, random_test, None), ('chronological', train, test, cutoff), ('chronological_purged', kept, test, cutoff)]:
        # Never relax this after seeing scores: an undersized cohort is explicit.
        minimum = len(tr) >= 50 and len(te) >= 20
        result[name] = {'train': tr, 'test': te, 'cutoff_height': boundary,
            'minimum_fit_eligible': minimum, 'train_removed': len(removed) if name == 'chronological_purged' else 0,
            'manifest': fa.split_manifest(tr, te), 'direct_overlap': fa.overlap_diagnostics(tr, te),
            'test_rings_with_reuse_ties': sum(len(set(r['reuse_counts'])) < len(r['reuse_counts']) for r in te)}
    return result, graph


def protocol_document(root=ROOT):
    matrix, _, records = fa.load_inputs(root)
    splits, graph = comparison_splits(matrix, records)
    vectors(records, matrix['feature_names'], 'index_ordered')
    return {'schema_version': 1, 'experiment_id': 'FA3',
        'question': 'Does replacing index-ordered reuse ties with equal midranks change retrospective label agreement within each declared split?',
        'prepared_at_semantics': 'Method, code, hashes and split identities must be committed before FA3 model fitting. FA2 baseline outcomes were already known.',
        'inputs': {fa.MATRIX: fa.MATRIX_SHA, fa.AUDIT: fa.AUDIT_SHA},
        'code_sha256': {name: fa.digest((root / name).read_bytes()) for name in (RUNNER, HELPER)},
        'reference_scorer_sha256': fa.SCORER_SHA,
        'feature_names': matrix['feature_names'], 'changed_column': 'reuse_rank_in_ring',
        'variants': list(VARIANTS), 'model': fa.MODEL,
        'tie_rule': 'Sort candidates by exact numeric amount/index before ranking reuse. Current ties retain index order. Equal midrank = (first tied position + last tied position) / (2*(ring_size-1)); all tied =>0.5. No other feature column changes.',
        'fit_rule': 'Fit StandardScaler and the fixed random forest on each training partition, then score its fixed test partition. No all-data refit, no tuning or feature selection.',
        'split_rule': 'Random exactly matches FA2. Chronological cutoff is the height at floor(0.8*N) in (height,key_image) order: train strictly below, test at/above, never split a boundary block. Purged uses the same chronological test set.',
        'graph_rule': 'Components use exact original shared outputs and same-transaction edges only in sampled rings with height<cutoff, including earlier unlabeled bridges. Quarantine training components touched by later labeled test output/transaction identities; no later unlabeled edges or outcome labels build components.',
        'graph_diagnostics': graph,
        'cohorts': {name: {key: value for key, value in row.items() if key not in ('train', 'test')} for name, row in splits.items()},
        'primary_measurement': 'Paired net change in correct selected outputs within each cohort; display all cohorts. Operational first-maximum selection and fractional credit for exact score ties are both recorded.',
        'secondary_measurements': ['All candidate scores, selected outputs and labels; paired outcomes in tied/untied test subgroups.', 'One-process elapsed/RSS per model; descriptive uniform/highest-index/minimum-reuse comparators.', 'Overlap, removed training fraction, component sizes, and feasibility denominators.'],
        'minimum_fit': {'training_rings': 50, 'test_rings': 20, 'otherwise': 'not_evaluated; do not relax the split'},
        'runtime_requirements': {'python': platform.python_version(), **{key: importlib.metadata.version(key) for key in ('numpy','scikit-learn','scipy')}},
        'resources': {'cohort_order': list(COHORTS), 'variant_order': list(VARIANTS), 'jobs': 1, 'fresh_process_per_fit': True, 'worker_timeout_seconds': 180},
        'limitations': LIMITATIONS}


def verify_registration(commit, root=ROOT):
    if not isinstance(commit, str) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Exact registration commit required')
    for name in (RUNNER, HELPER, PROTOCOL):
        raw = subprocess.run(['git','--no-replace-objects','show',f'{commit}:{name}'], cwd=root, check=True, capture_output=True, timeout=10).stdout
        if raw != (root / name).read_bytes():
            raise ValueError(f'{name} changed after method registration')
    protocol = fa.json_bytes((root / PROTOCOL).read_bytes())
    if protocol != protocol_document(root):
        raise ValueError('Method, versions or frozen split differ from registration')
    return protocol


def worker(commit, cohort, variant, destination):
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    protocol = verify_registration(commit)
    matrix, _, eligible = fa.load_inputs()
    splits, _ = comparison_splits(matrix, eligible)
    split = splits[cohort]
    if not split['minimum_fit_eligible']:
        raise ValueError('Registered cohort is too small to fit')
    train, test = split['train'], split['test']
    train_rows, test_rows = vectors(train, matrix['feature_names'], variant), vectors(test, matrix['feature_names'], variant)
    labels = [int(fa.output_key(c) == fa.output_key(r['deterministic_output'])) for r in train for c in r['candidates']]
    started = time.perf_counter()
    scaler = StandardScaler()
    x = scaler.fit_transform(np.asarray(train_rows))
    model = RandomForestClassifier(**protocol['model'])
    model.fit(x, labels)
    fit_seconds = time.perf_counter() - started
    if model.classes_.tolist() != [0, 1]:
        raise ValueError('Unexpected model class order')
    started = time.perf_counter()
    scores = model.predict_proba(scaler.transform(np.asarray(test_rows)))[:,1]
    score_seconds = time.perf_counter() - started
    rows, offset = [], 0
    for record in test:
        n = len(record['candidates'])
        outcome = fa.ring_outcome(record, scores[offset:offset+n].tolist())
        outcome['reuse_tied'] = len(set(record['reuse_counts'])) < len(record['reuse_counts'])
        rows.append(outcome)
        offset += n
    groups = {name: fa.summarize_outcomes(group) if group else None for name, group in
              [('reuse_tied',[r for r in rows if r['reuse_tied']]), ('reuse_untied',[r for r in rows if not r['reuse_tied']])]}
    result = {'cohort': cohort, 'variant': variant, 'summary': fa.summarize_outcomes(rows), 'subgroups': groups,
        'scaler': {'mean': scaler.mean_.tolist(),'scale':scaler.scale_.tolist()},
        'raw_train_vectors_sha256': fa.digest(fa.canonical(train_rows)), 'raw_test_vectors_sha256': fa.digest(fa.canonical(test_rows)),
        'effective_model_parameters': model.get_params(), 'feature_importances': model.feature_importances_.tolist(),
        'resources': {'fit_seconds': fit_seconds,'score_seconds': score_seconds,
            'process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)},
        'outcomes': rows}
    Path(destination).write_bytes(fa.canonical(result)+b'\n')


def run(commit, output_dir):
    protocol = verify_registration(commit)
    target = Path(output_dir).absolute()
    if target.exists() or target.is_symlink() or target.parent.resolve() != (ROOT / 'research/results').resolve():
        raise ValueError('Use a new direct result directory under research/results; existing results are preserved')
    started = datetime.now(timezone.utc).isoformat()
    matrix, audit, eligible = fa.load_inputs()
    splits, graph = comparison_splits(matrix, eligible)
    results = {}
    with tempfile.TemporaryDirectory(prefix='.fa3-', dir=target.parent) as folder:
        staging = Path(folder)
        for cohort in COHORTS:
            split = splits[cohort]
            result = {key:value for key,value in protocol['cohorts'][cohort].items()}
            results[cohort] = result
            if not split['minimum_fit_eligible']:
                result['state'] = 'not_evaluated'
                continue
            models = {}
            for variant in VARIANTS:
                name = f'{cohort}-{variant}.json'
                path = staging / name
                before = time.perf_counter()
                env = {**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1','PYTHONDONTWRITEBYTECODE':'1'}
                subprocess.run([sys.executable,str(ROOT / RUNNER),'_worker','--registered-commit',commit,'--cohort',cohort,'--variant',variant,'--output',str(path)],
                               cwd=ROOT, env=env, check=True, capture_output=True, timeout=180)
                model = fa.json_bytes(path.read_bytes())
                model['resources']['worker_wall_seconds'] = time.perf_counter() - before
                path.write_bytes(fa.canonical(model)+b'\n')
                models[variant] = model
            baseline, equal = [models[name]['outcomes'] for name in VARIANTS]
            result.update(state='evaluated', variants={name:{k:v for k,v in model.items() if k!='outcomes'} for name,model in models.items()},
                          paired=fa.paired_outcomes(baseline,equal), descriptive_comparators=fa.comparators(split['test']))
            result['paired_subgroups'] = {name: fa.paired_outcomes(a,b) if a else None for name,a,b in
                [('reuse_tied',[r for r in baseline if r['reuse_tied']],[r for r in equal if r['reuse_tied']]),
                 ('reuse_untied',[r for r in baseline if not r['reuse_tied']],[r for r in equal if not r['reuse_tied']])]}
        verify_registration(commit)
        summary = {'schema_version':1,'experiment_id':'FA3','started_at':started,'generated_at':datetime.now(timezone.utc).isoformat(),
            'source_revision':commit,'protocol_sha256':fa.digest((ROOT/PROTOCOL).read_bytes()),'input_hashes':protocol['inputs'],
            'source_scope':audit['scope'],'versions':protocol['runtime_requirements'],'model_settings':protocol['model'],
            'graph_diagnostics':graph,'cohorts':results,'limitations':LIMITATIONS,
            'artifacts':{p.name:{'sha256':fa.digest(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted(staging.iterdir())}}
        (staging/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
        staging.rename(target)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('prepare')
    execute=commands.add_parser('run');execute.add_argument('--registered-commit',required=True);execute.add_argument('--output-dir',type=Path,required=True)
    w=commands.add_parser('_worker');w.add_argument('--registered-commit',required=True);w.add_argument('--cohort',choices=COHORTS,required=True);w.add_argument('--variant',choices=VARIANTS,required=True);w.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='prepare':
        p=protocol_document();(ROOT/PROTOCOL).write_text(json.dumps(p,indent=2,allow_nan=False)+'\n')
        print(json.dumps({name:{k:row[k] for k in ('cutoff_height','train_removed','minimum_fit_eligible')}|{'train':row['manifest']['train']['rings'],'test':row['manifest']['test']['rings']} for name,row in p['cohorts'].items()}))
    elif args.command=='_worker':worker(args.registered_commit,args.cohort,args.variant,args.output)
    else:
        result=run(args.registered_commit,args.output_dir)
        print(json.dumps({name:row.get('paired',{'state':row['state']}) for name,row in result['cohorts'].items()}))


if __name__=='__main__':main()
