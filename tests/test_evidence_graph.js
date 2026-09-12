const test = require('node:test');
const assert = require('node:assert/strict');
const {buildGraph, key} = require('../docs/evidence-graph.js');

const fixture = () => ({key_image: 'ring', predicted_amount: '9007199254740993', predicted_output_index: '2', evidence: {
    candidate_count: 3, memory: {members: [{amount: '0', index: '2'}, {amount: '9007199254740993', index: '2'}, {amount: '0', index: '3'}], inputs: [{tx_hash: 'tx'}], resolution: {output: {amount: '0', index: '2'}, confidence: 1, pass_num: 0}},
    candidates: [], remaining_candidates: [{amount: '0', index: '2'}], related_rings: [{key_image: 'other', shared_outputs: 1}]
}});

test('output identities retain amount and large integers exactly', () => {
    const graph = buildGraph(fixture());
    const outputs = graph.nodes.filter(node => node.output);
    assert.equal(outputs.length, 3);
    assert.equal(new Set(outputs.map(node => node.id)).size, 3);
    assert.notEqual(key({amount: '0', index: '2'}), key({amount: '9007199254740993', index: '2'}));
    assert.ok(outputs.some(node => node.detail.includes('9007199254740993')));
});

test('hypothesis toggle preserves observed links and deterministic claims', () => {
    const graph = buildGraph(fixture(), {showHypotheses: false});
    assert.ok(graph.edges.some(edge => edge.kind === 'membership'));
    assert.ok(graph.edges.some(edge => edge.kind === 'overlap'));
    assert.ok(graph.edges.some(edge => edge.kind === 'deterministic'));
    assert.ok(!graph.edges.some(edge => ['prediction', 'hypothesis'].includes(edge.kind)));
    const row = fixture(); row.evidence.memory.resolution.pass_num = -1;
    assert.ok(!buildGraph(row).edges.some(edge => edge.kind === 'deterministic'));
    assert.ok(buildGraph(row).edges.some(edge => edge.kind === 'hypothesis'));
});

test('bounded graph retains prioritized known claims and reports omitted nodes', () => {
    const graph = buildGraph(fixture(), {maxCandidates: 2, maxRelated: 0});
    assert.equal(graph.nodes.filter(node => node.output).length, 2);
    assert.ok(graph.edges.some(edge => edge.kind === 'prediction'));
    assert.ok(graph.edges.some(edge => edge.kind === 'deterministic'));
    assert.ok(graph.notes.some(note => note.includes('bounded neighborhood')));
});

test('missing evidence or nonmember prediction cannot invent membership', () => {
    assert.equal(buildGraph({}).nodes.length, 0);
    const row = fixture(); row.predicted_output_index = '999';
    const graph = buildGraph(row);
    assert.ok(!graph.edges.some(edge => edge.kind === 'prediction'));
    assert.ok(graph.notes.some(note => note.includes('outside the exported membership')));
});
