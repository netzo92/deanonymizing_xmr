'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { flattenTasks, filterTasks, readFilters, sourceURL, baselineRows } = require('../docs/todo-results.js');
const ROOT = path.resolve(__dirname, '..');
const snapshot = JSON.parse(fs.readFileSync(path.join(ROOT, 'docs/research-progress.json')));

test('completion comes only from checked source state, not words suggesting progress', () => {
    const tasks = flattenTasks({ nodes: [{ id: 'brain/research/a.md', branch: 'research', title: 'Experiment', todos: [
        { text: '**FA2 — Ablation.** A prerequisite is completed; the experiment is planned.', done: false },
        { text: '**EA1 — Variation audit.** Measured results saved.', done: true },
        { text: 'Malformed status', done: 'true' },
    ] }] });
    assert.equal(tasks.length, 2);
    assert.deepEqual(filterTasks(tasks, { status: 'done' }).map(task => task.code), ['EA1']);
    assert.deepEqual(filterTasks(tasks, { status: 'open' }).map(task => task.code), ['FA2']);
});

test('task area, text, exact ID and completion filters compose without conflating references', () => {
    const tasks = flattenTasks({ nodes: [
        { id: 'brain/research/a.md', branch: 'research', title: 'Feature audit', todos: [{ text: '**FA2 — Paired ablation.** Compare masks.', done: false }, { text: '**FA4 — Broader coverage.** Depends on FA2.', done: false }] },
        { id: 'brain/tasks/dashboard.md', branch: 'tasks', title: 'Dashboard', todos: [{ text: '**D7 — Knowledge browser.** Implemented.', done: true }] },
    ] });
    assert.deepEqual(filterTasks(tasks, { task: 'FA2', status: 'all' }).map(task => task.code), ['FA2']);
    assert.equal(filterTasks(tasks, { query: 'FA2', status: 'open' }).length, 2);
    assert.equal(filterTasks(tasks, { branch: 'tasks', status: 'done' }).length, 1);
    assert.equal(filterTasks(tasks, { branch: 'research', status: 'done' }).length, 0);
});

test('shareable filters validate state and source links cannot escape the repository', () => {
    assert.deepEqual(readFilters('?status=all&branch=research&task=FA2&q=paired'), { status: 'all', branch: 'research', task: 'FA2', query: 'paired', id: '' });
    assert.equal(readFilters('?status=completed&branch=__proto__&task=<script>').status, 'open');
    assert.equal(readFilters('?branch=__proto__').branch, 'all');
    assert.equal(sourceURL('../private.db'), null);
    assert.equal(sourceURL('javascript:alert(1)'), null);
    assert.equal(sourceURL('//example.com'), null);
    assert.equal(sourceURL('brain/research/a.md', 'a'.repeat(40)), `https://github.com/netzo92/deanonymizing_xmr/blob/${'a'.repeat(40)}/brain/research/a.md`);
});

test('baseline display rejects inconsistent denominators and treats missing measurements as unknown', () => {
    const comparison = { denominator: 10, rows: [{ rings: 10, expected_correct: 8, expected_agreement: .8 }] };
    assert.equal(baselineRows(comparison).length, 1);
    assert.equal(baselineRows({ ...comparison, denominator: 9 }).length, 0);
    assert.equal(baselineRows({ denominator: 10, rows: [{ rings: 10, expected_correct: 8, expected_agreement: .9 }] }).length, 0);
    assert.equal(baselineRows({ denominator: 0, rows: [] }).length, 0);
});

test('every completed experiment links to the exact frozen result bytes', () => {
    assert.equal(snapshot.experiments.length, 3);
    for (const experiment of snapshot.experiments) {
        const bytes = fs.readFileSync(path.join(ROOT, experiment.artifact_path));
        assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), experiment.artifact_sha256, experiment.id);
        assert.equal(experiment.status, 'completed');
        assert.equal(experiment.evidence_kind, 'measured');
        assert.ok(fs.existsSync(path.join(ROOT, experiment.note_id)));
    }
    assert.ok(snapshot.theoretical_conclusions.every(item => item.status === 'theoretical'));
});

test('origin and feature cards reconcile with their original sample and feature counts', () => {
    const origin = snapshot.experiments.find(item => item.id === 'origin-audit');
    const originSource = JSON.parse(fs.readFileSync(path.join(ROOT, origin.artifact_path)));
    assert.equal(origin.metric.value, originSource.summary.returned_outputs);
    for (const key of ['sampled_contexts', 'returned_outputs', 'age_blocks_min', 'age_blocks_median', 'age_blocks_max']) assert.equal(origin.facts[key], originSource.summary[key]);
    const feature = snapshot.experiments.find(item => item.id === 'EA1');
    const featureSource = JSON.parse(fs.readFileSync(path.join(ROOT, feature.artifact_path)));
    assert.equal(feature.metric.value, featureSource.sampling.sampled_rings);
    assert.equal(feature.facts.sampled_candidates, featureSource.sampling.sampled_candidates);
    assert.equal(feature.facts.feature_count, featureSource.features.length);
    assert.equal(feature.facts.finite_values, featureSource.features.reduce((sum, item) => sum + item.finite_count, 0));
    assert.deepEqual(feature.facts.constant_features, featureSource.features.filter(item => item.globally_constant && item.finite_count === featureSource.sampling.sampled_candidates).map(item => item.name));
    assert.deepEqual(feature.facts.duplicate_feature_pairs, featureSource.duplicate_feature_pairs);
});

test('retrospective agreement rates retain exact tie-credit totals and the 12,076-ring denominator', () => {
    const comparison = snapshot.baseline_comparison;
    const source = JSON.parse(fs.readFileSync(path.join(ROOT, comparison.artifact_path)));
    assert.equal(comparison.metric, 'expected_agreement');
    assert.equal(comparison.denominator, source.summary.rings);
    assert.equal(comparison.candidate_rows, source.summary.candidate_rows);
    assert.equal(baselineRows(comparison).length, 4);
    for (const row of comparison.rows) {
        const original = source.summary.baselines[row.id];
        assert.equal(row.expected_correct, original.expected_correct);
        assert.deepEqual(row.expected_correct_exact, original.expected_correct_exact);
        assert.equal(row.expected_agreement, original.expected_accuracy);
        const expected = Number(BigInt(row.expected_correct_exact.numerator)) / Number(BigInt(row.expected_correct_exact.denominator)) / row.rings;
        assert.ok(Math.abs(expected - row.expected_agreement) < 1e-12);
        assert.equal(row.tie_rings, original.tie_rings);
    }
});

test('task date joins require exact whole-brain manifests and stable IDs', () => {
    const {matchingManifests, taskHistory} = require('../docs/todo-results.js');
    const manifest = {'brain/research/a.md':'a'.repeat(64)};
    assert.equal(matchingManifests({source_manifest:manifest}, {source_manifest:{...manifest}}), true);
    assert.equal(matchingManifests({source_manifest:manifest}, {source_manifest:{'brain/research/a.md':'b'.repeat(64)}}), false);
    assert.equal(matchingManifests({source_manifest:manifest}, {source_manifest:{...manifest,'brain/index.md':'a'.repeat(64)}}), false);
    assert.equal(matchingManifests({}, {}), false);
    const task = flattenTasks({nodes:[{id:'brain/research/a.md',todos:[{text:'**FA2 — Revised wording.** Current criteria.',done:false}]}]})[0];
    const history = {tasks:[{note_id:'brain/research/other.md',code:'FA2'},{note_id:task.noteId,code:'FA2',first_recorded:{at:'2026-09-11'}}]};
    assert.equal(taskHistory(task, history), history.tasks[1]);
    assert.equal(taskHistory({...task,code:'FA3'}, history), null);
});

test('canonical task links disambiguate equal short codes in different source notes', () => {
    const first = 'brain/research/a.md#FA2', second = 'brain/research/b.md#FA2';
    const tasks = [{id:first,code:'FA2',done:false},{id:second,code:'FA2',done:false}];
    const filters = readFilters(`?status=all&task=FA2&id=${encodeURIComponent(second)}`);
    assert.equal(filters.id, second);
    assert.deepEqual(filterTasks(tasks, filters).map(item => item.id), [second]);
    assert.equal(readFilters('?id=javascript:alert(1)').id, '');
});
