'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const api = require('../docs/era-view.js');
const manifest = JSON.parse(fs.readFileSync(require.resolve('../docs/protocol-eras.json')));
const first = manifest.eras[0], middle = manifest.eras[4], latest = manifest.eras.at(-1);
const clone = value => JSON.parse(JSON.stringify(value));
function exactExport() {
    return {summary: {total_rings: 100}, protocol_eras: {schema_version: 1, status: 'available', network: 'mainnet', classification_basis: 'mainnet_height_inferred', reconciliation: {matches_summary: true}, rows: manifest.eras.map((era, i) => ({...era, total_rings: i === 0 ? 100 : 0, blocks_scanned: i === 0 ? 21 : 0, deterministic_resolutions: i === 0 ? 70 : 0, deterministic_singleton_resolutions: i === 0 ? 60 : 0, deterministic_multimember_resolutions: i === 0 ? 10 : 0, hypothesis_resolutions: i === 0 ? 5 : 0, unresolved_reduced: i === 0 ? 15 : 0, unresolved_unchanged: i === 0 ? 10 : 0}))}};
}

test('manifest validates contiguous inclusive mainnet eras and distinct supported versions', () => {
    assert.equal(api.validateManifest(manifest), manifest);
    const gap = clone(manifest);gap.eras[1].start_height++;assert.throws(() => api.validateManifest(gap));
    const overlap = clone(manifest);overlap.eras[1].start_height--;assert.throws(() => api.validateManifest(overlap));
    const noGenesis = clone(manifest);noGenesis.eras[0].start_height = 1;assert.throws(() => api.validateManifest(noGenesis));
    for (const versions of [[], [0], [256], [1, 1], ['1']]) {const data = clone(manifest);data.eras[0].versions = versions;assert.throws(() => api.validateManifest(data));}
    const repeat = clone(manifest);repeat.eras[1].versions = [1];assert.throws(() => api.validateManifest(repeat));
    const closedLast = clone(manifest);closedLast.eras.at(-1).end_height = 9999999;assert.throws(() => api.validateManifest(closedLast));
});

test('height boundaries preserve genesis, inclusive transitions and unmapped input', () => {
    assert.equal(api.eraAtHeight(manifest, 0).id, first.id);
    for (let i = 1; i < manifest.eras.length; i++) {
        assert.equal(api.eraAtHeight(manifest, manifest.eras[i].start_height - 1).id, manifest.eras[i-1].id);
        assert.equal(api.eraAtHeight(manifest, manifest.eras[i].start_height).id, manifest.eras[i].id);
    }
    assert.equal(api.eraAtHeight(manifest, 3760540).id, latest.id);
    for (const height of [-1, null, undefined, '3760540', 2.5, Number.MAX_SAFE_INTEGER + 1]) assert.equal(api.eraAtHeight(manifest, height), null);
});

test('single-era fallback requires an explicit wholly contained scan and never prorates', () => {
    const data = {scope: {scan_start: 0, scan_end: 69900}, summary: {total_rings: 100, fully_resolved: 80}};
    const old = api.historicalEvidence(data, manifest, first);
    assert.equal(old.state, 'covered');assert.equal(old.method, 'single_era_fallback');assert.equal(old.metrics.total_rings, 100);
    assert.equal(old.metrics.deterministic_resolutions, null, 'legacy fully_resolved is not assumed deterministic');
    assert.match(old.assumption, /Assumes.*mainnet/);
    assert.equal(api.historicalEvidence(data, manifest, middle).state, 'no_coverage');
    data.scope.scan_end = middle.start_height;
    assert.equal(api.historicalEvidence(data, manifest, first).state, 'unknown');
    assert.equal(api.historicalEvidence(data, manifest, middle).state, 'unknown');
    delete data.scope;assert.equal(api.historicalEvidence(data, manifest, first).state, 'unknown');
});

test('exact aggregates keep singleton, multimember, hypotheses and unmeasured eras distinct', () => {
    const data = exactExport();
    data.protocol_eras.rows.push({id: 'unknown', total_rings: 7, orphan_resolution_claims: 2});
    data.summary.total_rings = 107;
    const result = api.historicalEvidence(data, manifest, first);
    assert.equal(result.state, 'covered');assert.equal(result.metrics.total_rings, 100);
    assert.equal(result.metrics.deterministic_singleton_resolutions, 60);
    assert.equal(result.metrics.deterministic_multimember_resolutions, 10);
    assert.equal(result.metrics.hypothesis_resolutions, 5);
    assert.equal(api.historicalEvidence(data, manifest, middle).state, 'no_coverage');
    data.protocol_eras.rows[4].blocks_scanned = 10;
    assert.equal(api.historicalEvidence(data, manifest, middle).state, 'covered', 'scanned blocks with no rings differ from no coverage');
});

test('unreconciled, unavailable or differently mapped aggregates do not trigger fallback', () => {
    for (const mutate of [d => d.protocol_eras.reconciliation.matches_summary = false, d => d.protocol_eras.status = 'unavailable', d => d.protocol_eras.rows[0].versions = [2], d => d.protocol_eras.rows[1].start_height++, d => d.protocol_eras.classification_basis = 'unknown', d => d.protocol_eras.network = 'testnet', d => d.protocol_eras.rows.push({...d.protocol_eras.rows[0]})]) {
        const data = exactExport();data.scope = {scan_start: 0, scan_end: 5};mutate(data);
        assert.equal(api.historicalEvidence(data, manifest, first).state, 'unknown');
    }
});

test('dataset mismatches and impossible category counts remain unknown despite a reconciliation flag', () => {
    for (const mutate of [d => {d.scope = {dataset_id: 'one'};d.protocol_eras.dataset_id = 'two';}, d => d.protocol_eras.rows[0].deterministic_multimember_resolutions = 101, d => d.protocol_eras.rows[0].unresolved_unchanged++, d => d.summary.total_rings++, d => d.protocol_eras.rows[0].deterministic_singleton_resolutions = '60']) {
        const data = exactExport();mutate(data);assert.equal(api.historicalEvidence(data, manifest, first).state, 'unknown');
    }
});

test('recorded versions are explicit; missing versions are inferred and disagreements flagged', () => {
    assert.equal(api.classifyBlock({height: latest.start_height + 50}, manifest).basis, 'mainnet_height_inferred');
    const result = api.classifyBlock({height: latest.start_height + 50, major_version: 1}, manifest);
    assert.equal(result.era.id, first.id);assert.equal(result.basis, 'recorded_version');assert.equal(result.mismatch, true);
    assert.equal(api.classifyBlock({height: latest.start_height, major_version: 99}, manifest).era, null);
    assert.equal(api.classifyBlock({height: latest.start_height, major_version: '16'}, manifest).era, null);
});

test('recent observations remain activity, keep incomplete counts unknown and exclude duplicate heights', () => {
    const base = {height: latest.start_height + 10, transaction_count: 3, ring_input_count: 5, detail_status: 'complete', ring_size_distribution: {'16': 5}};
    const data = {schema_version: 1, blocks: [base, {...base}, {...base, height: base.height + 1, detail_status: 'transaction_limit', ring_input_count: null}, {height: 'unknown'}]};
    const result = api.liveEvidence(data, manifest, latest);
    assert.equal(result.state, 'covered');assert.equal(result.blocks, 2);assert.equal(result.transactions, 6);assert.equal(result.countedInputs, 5);assert.equal(result.completeBlocks, 1);
    assert.deepEqual(result.ringSizes, [16]);assert.equal(result.duplicates, 1);assert.equal(result.unclassified, 1);
    assert.equal(result.deterministic_resolutions, undefined);
    assert.equal(api.liveEvidence(data, manifest, first).state, 'no_coverage');
    assert.equal(api.liveEvidence(null, manifest, first).state, 'unknown');
    data.blocks = [{height: base.height, detail_status: 'transaction_limit'}];
    const incomplete = api.liveEvidence(data, manifest, latest);assert.equal(incomplete.countedInputs, null);assert.equal(incomplete.transactions, null);
    const failed = api.liveEvidence({schema_version: 1, blocks: [], state: 'error', error: {message: 'RPC failed'}}, manifest, latest);
    assert.equal(failed.state, 'unknown');assert.match(failed.reason, /observer reports an error/);
});

test('frozen 2014 findings apply only to legacy and include completed measured work only', () => {
    const data = {schema_version: 1, scope: {scan_start: 0, scan_end: 58900}, experiments: [{id: 'EA1', status: 'completed', evidence_kind: 'measured'}, {id: 'future', status: 'planned', evidence_kind: 'measured'}, {id: 'paper', status: 'completed', evidence_kind: 'external'}]};
    assert.deepEqual(api.frozenEvidence(data, manifest, first).experiments.map(item => item.id), ['EA1']);
    assert.equal(api.frozenEvidence(data, manifest, latest).state, 'no_coverage');
    data.scope.scan_end = latest.start_height;assert.equal(api.frozenEvidence(data, manifest, first).state, 'unknown');
});

test('unsafe and credential-bearing links are rejected while official source and local task links survive', () => {
    for (const value of ['javascript:alert(1)', 'data:text/html,test', '//evil.test', 'https://user:secret@example.com/', 'https://example.com/\nfoo', 'brain/../../secrets', 'file:///private/file']) assert.equal(api.safeURL(value), null);
    assert.equal(api.safeURL('todos.html?status=all&task=ER3#tasks'), 'todos.html?status=all&task=ER3#tasks');
    assert.match(api.safeURL('brain/research/evaluation.md'), /^https:\/\/github.com\/netzo92\//);
    assert.equal(api.safeURL('https://www.getmonero.org/example'), 'https://www.getmonero.org/example');
});

test('failed refresh keeps each last good snapshot independently and recovery clears failure', () => {
    const old = {historical: {data: {snapshot: 1}, error: null}, live: {data: {snapshot: 2}, error: null}};
    const results = [{status: 'fulfilled', value: manifest}, {status: 'rejected', reason: Error('HTTP 503')}, {status: 'fulfilled', value: {snapshot: 3}}, {status: 'rejected', reason: Error('HTTP 404')}];
    const next = api.applyResults(old, results);
    assert.equal(next.historical.data, old.historical.data);assert.equal(next.historical.error, 'HTTP 503');
    assert.equal(next.live.data.snapshot, 3);assert.equal(next.frozen.data, null);
    results[1] = {status: 'fulfilled', value: {snapshot: 4}};
    const recovered = api.applyResults(next, results);assert.equal(recovered.historical.error, null);assert.equal(recovered.historical.data.snapshot, 4);
});

test('shareable selection accepts known eras only and source validation refuses malformed replacement data', () => {
    assert.equal(api.readSelection('?era=clsag', manifest), 'clsag');
    assert.equal(api.readSelection('?era=<script>', manifest), first.id);
    assert.throws(() => api.validateSource('historical', {}));
    assert.throws(() => api.validateSource('live', {schema_version: 1, blocks: {}}));
    assert.throws(() => api.validateSource('frozen', {schema_version: 2, experiments: []}));
});

test('published research snapshot remains exclusively legacy under the pinned mapping', () => {
    const frozen = JSON.parse(fs.readFileSync(require.resolve('../docs/research-progress.json')));
    assert.equal(api.containedEra(manifest, frozen.scope).id, 'legacy_v1');
    assert.equal(api.frozenEvidence(frozen, manifest, first).experiments.length, 3);
    manifest.eras.slice(1).forEach(era => assert.equal(api.frozenEvidence(frozen, manifest, era).state, 'no_coverage'));
});
