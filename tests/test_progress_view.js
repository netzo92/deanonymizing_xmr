const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const progress = require('../docs/progress-view.js');

const snapshot = (overrides = {}) => ({dataset_id: 'dataset-a', timestamp: '2026-09-12T00:00:00Z',
    total_rings: 100, deterministic_resolutions: 70, blocks_scanned: 10, ...overrides});
const data = (history = [], rows = []) => ({scope: {dataset_id: 'dataset-a'}, history,
    prediction_browser: {rows, total: 900, limit: 200, order: 'newest_first'}});
const row = (key, members = [], inputs = [], related = []) => ({key_image: key, prediction_id: key,
    evidence: {memory: {members, inputs}, related_rings: related}});
const output = (amount, index) => ({amount, index});

test('timeline compares only explicit matching datasets and observation timestamps', () => {
    const report = progress.timeline(data([
        snapshot({dataset_id: 'dataset-b', deterministic_resolutions: 999}),
        snapshot({dataset_id: undefined}), snapshot({timestamp: '2014-06-01'}),
        snapshot({timestamp: '2026-09-12T00:10:00Z', deterministic_resolutions: 80, total_rings: 120}),
        snapshot(),
    ]));
    assert.deepEqual(report.points.map(point => point.deterministic), [70, 80]);
    assert.deepEqual(report.points.map(point => point.delta), [null, 10]);
    assert.equal(report.points[1].totalDelta, 20);
    assert.deepEqual(report.exclusions, {otherDataset: 1, unknownDataset: 1, invalidTimestamp: 1, duplicateSnapshot: 0, conflictingTimestamp: 0});
    assert.equal(progress.timeline({...data([snapshot()]), scope: {}}).points.length, 0);
});

test('legacy stored totals cannot masquerade as deterministic labels; corrections remain negative', () => {
    const points = progress.timeline(data([
        snapshot(), snapshot({timestamp: '2026-09-12T00:01:00Z', deterministic_resolutions: 65}),
        snapshot({timestamp: '2026-09-12T00:02:00Z', deterministic_resolutions: undefined, fully_resolved: 90}),
        snapshot({timestamp: '2026-09-12T00:03:00Z', deterministic_resolutions: 80}),
        snapshot({timestamp: '2026-09-12T00:04:00Z', deterministic_resolutions: 101}),
    ])).points;
    assert.deepEqual(points.map(point => point.delta), [null, -5, null, null, null]);
    assert.deepEqual(points.map(point => point.deterministic), [70, 65, null, 80, null]);
});

test('duplicate timestamps are deduplicated and conflicting timestamps are excluded', () => {
    const report = progress.timeline(data([snapshot(), snapshot(),
        snapshot({timestamp: '2026-09-12T00:01:00Z'}),
        snapshot({timestamp: '2026-09-12T00:01:00Z', deterministic_resolutions: 60}),
    ]));
    assert.equal(report.points.length, 1);
    assert.equal(report.exclusions.duplicateSnapshot, 1);
    assert.equal(report.exclusions.conflictingTimestamp, 1);
});

test('current summary adds a changed explicitly scoped export without duplicating unchanged observations', () => {
    const fixture = data([snapshot()]);
    fixture.scope.exported_at = '2026-09-12T00:01:00Z';
    fixture.summary = {total_rings: 100, deterministic_resolutions: 70, blocks_scanned: 10};
    assert.equal(progress.timeline(fixture).points.length, 1);
    fixture.summary.deterministic_resolutions = 75;
    const points = progress.timeline(fixture).points;
    assert.equal(points.length, 2); assert.equal(points[1].delta, 5);
    assert.equal(progress.timeline({...fixture, history: []}).points.length, 1);
    fixture.scope.exported_at = '2014-06-01';
    assert.equal(progress.timeline({...fixture, history: []}).points.length, 0);
});

test('singleton and multi-member counts require exported fields and never infer historical progress', () => {
    const breakdown = {original_singleton_rings: 60, original_multimember_rings: 40,
        deterministic_singleton_resolutions: 60, deterministic_multimember_resolutions: 10};
    const fixture = data([snapshot(), snapshot({timestamp: '2026-09-12T00:01:00Z', ...breakdown}),
        snapshot({timestamp: '2026-09-12T00:02:00Z', ...breakdown, deterministic_resolutions: 75, deterministic_multimember_resolutions: 15}),
        snapshot({timestamp: '2026-09-12T00:03:00Z', ...breakdown, deterministic_multimember_resolutions: 20})]);
    const points = progress.timeline(fixture).points;
    assert.deepEqual(points.map(point => point.deterministicMultimember), [null, 10, 15, null]);
    assert.deepEqual(points.map(point => point.multimemberDelta), [null, null, 5, null]);
    assert.equal(points[0].originalSingleton, null); assert.equal(points[1].originalMultimember, 40);
    fixture.history = [snapshot()]; fixture.scope.exported_at = '2026-09-12T00:01:00Z';
    fixture.summary = {...snapshot(), ...breakdown};
    assert.equal(progress.timeline(fixture).points.length, 2, 'New breakdown adds information even when headline totals match');
});

test('output identities preserve both exact decimal strings and reject lossy numeric values', () => {
    assert.equal(progress.outputKey(output('9007199254740993', '9007199254740995')), '["9007199254740993","9007199254740995"]');
    assert.notEqual(progress.outputKey(output('0', '2')), progress.outputKey(output('1', '2')));
    assert.equal(progress.outputKey(output(0, 2)), '["0","2"]');
    for (const value of [9007199254740992, NaN, Infinity, -1, '01', '1.0', '', null, '<script>']) {
        assert.equal(progress.outputKey(output(value, '2')), null);
    }
});

test('direct output and transaction groups deduplicate rings without transitive grouping', () => {
    const a = row('a', [output('0', '1'), output('0', '1')], [{tx_hash: 'tx'}, {tx_hash: 'tx'}]);
    const b = row('b', [output('0', '1'), output('0', '2')], [{tx_hash: 'tx'}]);
    const c = row('c', [output('0', '2'), output('1', '1')]);
    const report = progress.relationships(data([], [a, {...a, prediction_id: 'older'}, b, c]));
    assert.equal(report.scope.records, 4); assert.equal(report.scope.uniqueRings, 3);
    assert.equal(report.scope.repeatedRecords, 1); assert.equal(report.rings.get('a').prediction_id, 'a');
    assert.deepEqual(report.transactions.map(group => group.members), [['a', 'b']]);
    assert.deepEqual(report.outputs.map(group => group.members), [['a', 'b'], ['b', 'c']]);
    assert.ok(!report.outputs.some(group => group.members.length === 3));
});

test('reported pairs deduplicate reciprocal counts without inventing output identities or summed weights', () => {
    const report = progress.relationships(data([], [
        row('a', [], [], [{key_image: 'b', shared_outputs: 3}, {key_image: 'outside', shared_outputs: 2}]),
        row('b', [], [], [{key_image: 'a', shared_outputs: 3}, {key_image: 'a', shared_outputs: 4}]),
    ]));
    assert.equal(report.related.length, 2); assert.equal(report.scope.externalNeighbors, 1);
    assert.deepEqual(report.related[0].members, ['a', 'b']);
    assert.deepEqual(report.related[0].counts, [3, 4]);
    assert.deepEqual(report.related[0].reporters, ['a', 'b']);
    assert.equal(report.outputs.length, 0);
});

test('bounded and unavailable evidence retain visible diagnostics instead of synthetic edges', () => {
    const a = row('a', [output('0', '2'), output(9007199254740992, '1')]);
    Object.assign(a.evidence, {candidate_count: 5, input_count: 2, related_rings_truncated: true});
    const report = progress.relationships(data([], [a, {key_image: 'b'}, {}, null]));
    assert.equal(report.scope.boundedMemberships, 1); assert.equal(report.scope.boundedInputs, 1);
    assert.equal(report.scope.boundedRelated, 1); assert.equal(report.scope.missingEvidence, 1);
    assert.equal(report.scope.invalidOutputs, 1); assert.equal(report.scope.missingRingRecords, 2);
    assert.equal(report.related.length, 0); assert.equal(report.outputs.length, 0);
});

// A small DOM contract verifies lifecycle, safe text construction, and inspection without a browser dependency.
class Node {
    constructor(document, tag, text = null) {
        this.ownerDocument = document; this.tagName = tag.toUpperCase(); this.nodeType = tag === '#text' ? 3 : 1;
        this.children = []; this.parentNode = null; this.attributes = {}; this.dataset = {}; this.events = {}; this._text = text;
    }
    append(...children) {
        for (let child of children) {
            if (typeof child === 'string') child = this.ownerDocument.createTextNode(child);
            child.parentNode = this; this.children.push(child);
        }
    }
    replaceChildren(...children) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = null; this.append(...children); }
    set textContent(value) { this.replaceChildren(); this._text = String(value); }
    get textContent() { return (this._text || '') + this.children.map(child => child.textContent).join(''); }
    set innerHTML(_) { throw new Error('HTML insertion is forbidden'); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(name, fn) { this.events[name] = fn; }
    focus() { this.ownerDocument.focused = this; }
}
const documentFixture = () => {
    const document = {createElement(tag) { return new Node(this, tag); },
        createElementNS(_, tag) { return new Node(this, tag); }, createTextNode(value) { return new Node(this, '#text', String(value)); }};
    return document;
};
const descendants = node => [node, ...node.children.flatMap(descendants)];

test('mount, update and remount preserve safe text and inspect the exact exported row', () => {
    const document = documentFixture(), host = document.createElement('div'), inspected = [];
    const malicious = '<img src=x onerror=alert(1)>';
    const a = row('ring-a', [], [{tx_hash: malicious}]), b = row('ring-b', [], [{tx_hash: malicious}]);
    const initial = data([snapshot()], [a, b]);
    const app = progress.mount(host, initial, {onInspect: value => inspected.push(value)});
    assert.match(host.textContent, /wallet or address ownership/);
    assert.ok(host.textContent.includes(malicious));
    assert.ok(!descendants(host).some(node => node.tagName === 'IMG'));
    const inspect = descendants(host).find(node => node.attributes['aria-label'] === 'Inspect ring ring-a');
    inspect.events.click(); assert.equal(inspected[0], a);
    app.update(data([snapshot({deterministic_resolutions: 5})], []));
    assert.equal(app.getReport().timeline.points[0].deterministic, 5);
    assert.match(host.textContent, /No direct groups of this kind/);
    const replacement = progress.mount(host, initial);
    app.destroy(); assert.equal(host.children.length, 1);
    app.update(data()); assert.equal(replacement.getReport().relationships.scope.uniqueRings, 2);
    replacement.destroy(); assert.equal(host.children.length, 0);
});

test('real export contract keeps timeline cohorts isolated and output groups directly supported', () => {
    const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, '../docs/data.json'), 'utf8'));
    const report = progress.timeline(fixture), groups = progress.relationships(fixture);
    assert.equal(report.datasetId, fixture.scope.dataset_id);
    for (const point of report.points) {
        assert.ok(point.deterministic === null || point.deterministic <= point.total);
        assert.ok(point.observedAt.endsWith('Z'));
    }
    for (const group of groups.outputs) {
        assert.ok(group.members.length > 1);
        for (const key of group.members) {
            assert.ok(groups.rings.get(key).evidence.memory.members.some(value => progress.outputKey(value) === group.key));
        }
    }
    assert.equal(groups.scope.records, fixture.prediction_browser.rows.length);
});
