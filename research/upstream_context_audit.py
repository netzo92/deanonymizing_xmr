"""Reconcile existing real observations used alongside the pinned source audit.

Reads JSON artifacts only; performs no RPC, database writes, training or scoring.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import median


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def audit(origins_path, observations_path, source_pin_path):
    origins, origins_sha = read(origins_path)
    live, live_sha = read(observations_path)
    source, source_sha = read(source_pin_path)
    sample = origins['sample']
    ages, proxies, current_unlocked = [], [], []
    gamma = set()
    for row in sample:
        height = row.get('origin', {}).get('height')
        input_height = row.get('input_height')
        if type(height) is not int or type(input_height) is not int or input_height < height:
            raise ValueError('Every age observation needs valid creation/input heights')
        age = input_height - height
        if age != row.get('actual_age_blocks'):
            raise ValueError('Recorded origin age does not reconcile')
        ages.append(age)
        proxies.append(row['legacy_relative_age_proxy'])
        current_unlocked.append(row['origin'].get('unlocked'))
        gamma.add((row['gamma_log_likelihood'], row['gamma_recent_window'], row['gamma_surprisal']))
    rings = Counter()
    for block in live['blocks']:
        if block['detail_status'] != 'complete':
            raise ValueError('This frozen context requires complete ring counts')
        distribution = block['ring_size_distribution']
        if sum(distribution.values()) != block['ring_input_count']:
            raise ValueError('Per-block ring counts do not reconcile')
        if sum(int(size) * n for size, n in distribution.items()) != block['ring_member_count']:
            raise ValueError('Per-block membership counts do not reconcile')
        rings.update(distribution)
    window = live['window']
    if (sum(rings.values()) != window['ring_input_count'] or
        sum(b['transaction_count'] for b in live['blocks']) != window['transaction_count'] or
        len(live['blocks']) != window['block_count']):
        raise ValueError('Observer window counts do not reconcile')
    return {
        'audit_version': 1,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'audit_kind': 'reconciliation_of_existing_observations_not_prediction_evaluation',
        'upstream': {'tag': source['tag'], 'commit': source['commit'], 'pin_sha256': source_sha},
        'source_artifacts': {'origins': {'path': str(origins_path), 'sha256': origins_sha},
                             'observations': {'path': str(observations_path), 'sha256': live_sha}},
        'historical_origins': {
            'sample_count': len(sample), 'all_nonzero_amounts': all(int(r['amount']) > 0 for r in sample),
            'source_scan': origins['scan'],
            'actual_age_blocks': {'min': min(ages), 'median': median(ages), 'max': max(ages)},
            'relative_age_proxy': {'min': min(proxies), 'max': max(proxies)},
            'actual_age_over_15_blocks': sum(age > 15 for age in ages),
            'distinct_gamma_feature_triples': len(gamma),
            'rpc_currently_unlocked_true': sum(v is True for v in current_unlocked),
            'interpretation': 'Historical output ages reconcile with recorded RPC creation heights. Current unlocked flags are not historical eligibility labels. This is a fixed 40-output sample, not a model-quality test.',
        },
        'current_observations': {
            'observed_success_at': live['last_success_at'], 'source': live['source'],
            'window': window, 'ring_size_distribution': dict(sorted(rings.items(), key=lambda p: int(p[0]))),
            'interpretation': 'Current confirmed-block activity from one external RPC source. No actual-spend labels; the node software version of that source was not established by this snapshot.',
        },
        'limitations': [
            'Historical origins and current blocks are separate populations and observation procedures.',
            'No model changes, new labels, accuracy gains, ownership claims or forecasts are established.',
            'The pinned upstream release does not identify which wallet software created a transaction.',
            'The origin audit v1 preserved the main-file hash but did not independently fingerprint WAL state.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origins', default='research/results/output_origins_2026-09-11.json')
    parser.add_argument('--observations', default='research/results/upstream_observations_2026-09-11.json')
    parser.add_argument('--source-pin', default='references/monero-source.json')
    parser.add_argument('--output', default='research/results/upstream_context_2026-09-11.json')
    args = parser.parse_args()
    result = audit(args.origins, args.observations, args.source_pin)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
