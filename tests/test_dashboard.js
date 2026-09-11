'use strict';

// Run with: node --test tests/test_dashboard.js
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { outcome, acceptance, evidenceCategories, historyCohorts, filterRows, identity, sameOutput, percentage } = require('../docs/dashboard.js');

test('amount/index identity stays lossless and includes the amount bucket', () => {
    const a = { amount: '18446744073709551615', index: '9007199254740993' };
    const b = { amount: '18446744073709551614', index: '9007199254740993' };
    assert.equal(identity(a), '(18446744073709551615, 9007199254740993)');
    assert.equal(sameOutput(a, b), false);
    assert.equal(sameOutput({ amount: '0', index: '12' }, { amount: 0, index: 12 }), true);
    assert.equal(identity({ amount: 0 }), 'Unknown');
});

test('verification and threshold decisions remain independent, with unknown fields preserved', () => {
    assert.equal(outcome({ verified: false, correct: true }), 'pending');
    assert.equal(outcome({ verified: true, correct: false, accepted: false }), 'wrong');
    assert.equal(outcome({ verified: 1, correct: 1 }), 'correct');
    assert.equal(outcome({ verified: true, correct: null }), 'unknown');
    assert.equal(outcome({}), 'unknown');
    assert.equal(acceptance({ accepted: false, verified: false }), 'below');
    assert.equal(acceptance({}), 'unknown');
});

test('legacy evidence totals never become deterministic counts', () => {
    const categories = evidenceCategories({ total_rings: 20, fully_resolved: 19 });
    assert.equal(categories.complete, false);
    assert.equal(categories.entries[0][1], undefined);
    assert.equal(categories.reconciles, false);
});

test('evidence categories reconcile separately from overlapping conflict flags', () => {
    assert.equal(evidenceCategories({ total_rings: 10, deterministic_resolutions: 5, hypothesis_resolutions: 2, unresolved_reduced: 2, unresolved_unchanged: 1, conflict_rings: 4 }).reconciles, true);
    assert.equal(evidenceCategories({ total_rings: 0, deterministic_resolutions: 0, hypothesis_resolutions: 0, unresolved_reduced: 0, unresolved_unchanged: 0 }).reconciles, true);
    assert.equal(evidenceCategories({ total_rings: 1, deterministic_resolutions: 2, hypothesis_resolutions: 0, unresolved_reduced: 0, unresolved_unchanged: 0 }).reconciles, false);
    assert.equal(percentage(0, 0), 'N/A');
    assert.equal(percentage(0, 5), '0.0%');
});

test('filters combine across historical rows and never convert missing numeric metadata to zero', () => {
    const rows = [
        { prediction_id: 1, key_image: 'AAA', tx_hashes: ['Tx-B'], run_id: 'run-1', predicted_amount: '0', predicted_output_index: '9007199254740993', confidence: .92, block_height: 20, original_ring_size: 11, verified: true, correct: false, accepted: false },
        { prediction_id: 2, key_image: 'AAA', tx_hashes: ['Tx-B'], run_id: 'run-2', confidence: .97, block_height: 20, original_ring_size: 11, verified: false, correct: null, accepted: true },
        { prediction_id: 3, key_image: 'CCC', confidence: null, block_height: null, original_ring_size: null, verified: null, accepted: null },
    ];
    assert.equal(filterRows(rows).length, 3);
    assert.equal(filterRows(rows, { minHeight: '0' }).length, 2);
    assert.equal(filterRows(rows, { minScore: '0' }).length, 2);
    assert.deepEqual(filterRows(rows, { query: '9007199254740993' }).map(row => row.prediction_id), [1]);
    assert.deepEqual(filterRows(rows, { query: 'tx-b', minScore: '.9', minHeight: 20, maxHeight: 20, ringSize: 11, outcome: 'wrong', acceptance: 'below' }).map(row => row.prediction_id), [1]);
    assert.deepEqual(filterRows(rows, { query: 'run-2', minScore: '.95', outcome: 'pending' }).map(row => row.prediction_id), [2]);
    assert.deepEqual(filterRows(rows, { outcome: 'unknown', acceptance: 'unknown' }).map(row => row.prediction_id), [3]);
    assert.equal(filterRows(rows, { minHeight: 21 }).length, 0);
});

test('history keeps unrelated and unknown datasets out of the current timeline', () => {
    const snapshots = [ { dataset_id: 'a', blocks_scanned: 1 }, { blocks_scanned: 2 }, { dataset_id: 'b', blocks_scanned: 3 }, { dataset_id: 'a', blocks_scanned: 4 } ];
    const cohorts = historyCohorts(snapshots, 'a');
    assert.deepEqual(cohorts.current.map(item => item.blocks_scanned), [1, 4]);
    assert.deepEqual(cohorts.unknown.map(item => item.blocks_scanned), [2]);
    assert.deepEqual(cohorts.other.map(item => item.blocks_scanned), [3]);
    assert.equal(historyCohorts(snapshots, null).current.length, 0);
});
