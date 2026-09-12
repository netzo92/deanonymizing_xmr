'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const analytics = require('../docs/research-analytics.js');

const row = (values = {}) => ({ confidence: 0.95, accepted: true, verified: false, correct: null, ...values });
const data = (rows, runs = [], total = rows.length) => ({
    schema_version: 2, scope: { dataset_id: 'synthetic', exported_at: '2026-09-11T00:00:00Z' },
    prediction_browser: { rows, runs, total, order: 'newest_first' },
});
const near = (a, b, epsilon = 1e-10) => assert.ok(Math.abs(a - b) <= epsilon, `${a} differs from ${b}`);

test('Wilson interval uses observed correct/wrong outcomes and refuses absent denominators', () => {
    assert.equal(analytics.wilson(0, 0), null);
    assert.equal(analytics.wilson(5, 4), null);
    assert.equal(analytics.wilson(null, 10), null);
    assert.equal(analytics.wilson(1.5, 10), null);
    const interval = analytics.wilson(5, 10);
    near(interval.low, 0.236593090512564);
    near(interval.high, 0.763406909487436);
    near(analytics.wilson(0, 10).low, 0);
    near(analytics.wilson(10, 10).high, 1);
});

test('threshold retention preserves acceptance and uses all included records as denominator', () => {
    const rows = [
        row({ confidence: 0.98, accepted: false, verified: true, correct: true }),
        row({ confidence: 0.96, accepted: true, verified: true, correct: false }),
        row({ confidence: 0.95, accepted: true }),
        row({ confidence: 0.4 }),
        row({ confidence: null }),
    ];
    const report = analytics.analyze(data(rows, [], 1000));
    assert.equal(report.retained.total, 3);
    assert.equal(report.retained.accepted, 2);
    assert.equal(report.retained.belowRunThreshold, 1);
    assert.equal(report.retained.correct, 1);
    assert.equal(report.retained.wrong, 1);
    assert.equal(report.retained.pending, 1);
    assert.equal(report.retained.accuracy, 0.5);
    assert.equal(report.retained.coverage, 3 / 5);
    assert.equal(report.retained.coverageDenominator, 5);
    assert.equal(report.scope.omittedRecords, 995);
    assert.equal(report.filter.unknownScoresExcluded, 1);
    assert.equal(rows[0].accepted, false);
});

test('unknown and malformed scores never become zero, including at threshold zero', () => {
    const rows = [0, 1, null, undefined, '', '0.99', NaN, Infinity, -0.1, 1.1].map(confidence => row({ confidence }));
    const report = analytics.analyze(data(rows), { threshold: 0 });
    assert.equal(report.retained.total, 2);
    assert.equal(report.filter.unknownScoresExcluded, 8);
    assert.equal(report.scoreDistribution.unknown, 8);
    assert.equal(report.retained.coverage, 0.2);
});

test('verification status takes precedence over stale correct flags', () => {
    const summary = analytics.summarize([
        row({ verified: false, correct: true }),
        row({ verified: true, correct: null }),
        row({ verified: 1, correct: 1 }),
        row({ verified: 1, correct: 0 }),
        row({ verified: null, correct: true }),
    ]);
    assert.equal(summary.pending, 1);
    assert.equal(summary.unknown, 2);
    assert.equal(summary.verified, 2);
    assert.equal(summary.accuracy, 0.5);
    assert.equal(analytics.summarize([]).accuracy, null);
    assert.equal(analytics.summarize([row()]).interval, null);
});

test('bins account for edges, pending outcomes, and unknown scores without fictitious calibration', () => {
    const rows = [
        row({ confidence: 0, verified: true, correct: false }),
        row({ confidence: 0.1, verified: true, correct: true }),
        row({ confidence: 0.99 }), row({ confidence: 1 }), row({ confidence: null }),
    ];
    const result = analytics.scoreBins(rows);
    assert.equal(result.bins[0].total, 1);
    assert.equal(result.bins[0].wrong, 1);
    assert.equal(result.bins[0].meanScore, 0);
    assert.equal(result.bins[1].correct, 1);
    assert.equal(result.bins[9].total, 2);
    assert.equal(result.bins[9].pending, 2);
    assert.equal(result.bins[9].verified, 0);
    assert.equal(result.bins[9].accuracy, null);
    assert.equal(result.bins[9].includesUpper, true);
    assert.equal(result.bins.reduce((sum, bin) => sum + bin.total, result.unknown), rows.length);
    assert.throws(() => analytics.scoreBins(rows, 0), RangeError);
});

test('bin score means distinguish all scored records from observed verification outcomes', () => {
    const result = analytics.scoreBins([
        row({ confidence: 0.91, verified: true, correct: true }),
        row({ confidence: 0.95, verified: true, correct: false }),
        row({ confidence: 0.99, verified: false }),
        row({ confidence: 0.97, verified: true, correct: null }),
    ]).bins[9];
    near(result.meanScore, 0.955);
    near(result.meanVerifiedScore, 0.93);
    assert.equal(result.verified, 2);
    assert.equal(result.accuracy, 0.5);
    assert.equal(analytics.scoreBins([row({ confidence: 0.99 })]).bins[9].meanVerifiedScore, null);
});

test('cohort filters expose legacy and absent metadata and keep the global subset denominator', () => {
    const runs = [{ run_id: 'old', scan_height: null, metadata: { origin: 'legacy_import' } },
        { run_id: 'new', scan_height: 100, metadata: { origin: 'scorer' } }];
    const rows = [row({ run_id: 'old', scan_height: null }), row({ run_id: 'new', scan_height: 100 }), row()];
    const initial = analytics.analyze(data(rows, runs));
    assert.match(initial.cohorts[0].label, /Legacy import.*scan unknown/);
    assert.match(initial.cohorts[2].label, /Unknown run.*scan unknown/);
    const selected = analytics.analyze(data(rows, runs), { cohort: initial.cohorts[1].key });
    assert.equal(selected.retained.total, 1);
    assert.equal(selected.retained.coverage, 1 / 3);
    assert.equal(selected.filter.cohortRecords, 1);
    assert.equal(selected.scoreDistribution.bins.reduce((sum, bin) => sum + bin.total, 0), 1);
});

test('verification cohorts keep retrospective, unknown, and later-cutoff evidence distinct', () => {
    const rows = [
        row({ verified: true, correct: true, verification_provenance: 'legacy_unknown' }),
        row({ verified: true, correct: true, verification_provenance: 'deterministic_resolution', scan_height: 0, verification_height: 1 }),
        row({ verified: true, correct: false, verification_provenance: 'deterministic_resolution', scan_height: 2, verification_height: 2 }),
        row({ verified: true, correct: true, verification_provenance: 'deterministic_resolution', scan_height: null, verification_height: 5 }),
    ];
    const labels = analytics.analyze(data(rows)).byVerification.map(value => value.label);
    assert.ok(labels.includes('Legacy verification · provenance/time unknown'));
    assert.ok(labels.includes('Deterministic verification · later scanned cutoff'));
    assert.ok(labels.includes('Deterministic verification · same/earlier cutoff'));
    assert.ok(labels.includes('Deterministic verification · scan timing unknown'));
});

test('candidate ambiguity needs complete alternatives and retains exact amount/index identity', () => {
    const complete = row({ candidates_total: 2, candidates_truncated: false, candidates: [
        { amount: '18446744073709551615', index: '9007199254740993', score: 0.2 },
        { amount: '18446744073709551614', index: '9007199254740993', score: 0.8 },
    ] });
    near(analytics.candidateAmbiguity(complete).margin, 0.6);
    assert.equal(analytics.candidateAmbiguity({ ...complete, candidates_truncated: true }).margin, null);
    assert.equal(analytics.candidateAmbiguity({ ...complete, candidates_total: 3 }).margin, null);
    assert.equal(analytics.candidateAmbiguity({ ...complete, candidates_truncated: undefined }).margin, null);
    assert.equal(analytics.candidateAmbiguity(complete, { metadata: { origin: 'legacy_import' } }).margin, null);
    assert.equal(analytics.candidateAmbiguity({ ...complete, candidates: [complete.candidates[0], { ...complete.candidates[1], score: null }] }).margin, null);
    assert.equal(analytics.candidateAmbiguity({ ...complete, candidates: [complete.candidates[0], complete.candidates[0]] }).margin, null);
    assert.equal(analytics.candidateAmbiguity({ candidates_total: 1, candidates_truncated: false, candidates: [complete.candidates[0]] }).margin, null);
    assert.equal(analytics.candidateAmbiguity({ ...complete, candidates: complete.candidates.map(value => ({ ...value, score: 0.8 })) }).margin, 0);
});

test('current reductions deduplicate repeated rings and use full counts despite truncated arrays', () => {
    const a = row({ key_image: 'A', evidence: { candidate_count: 20, remaining_candidate_count: 8,
        candidates_truncated: true, remaining_candidates_truncated: true, remaining_candidates: [{ amount: '0', index: '1' }] } });
    const b = row({ key_image: 'B', evidence: { candidate_count: 3, remaining_candidate_count: 0, conflicts: ['No candidates remain'] } });
    const result = analytics.reductionSummary([a, a, b, row()]);
    assert.equal(result.uniqueRings, 2);
    assert.equal(result.missingIdentities, 1);
    assert.equal(result.conflicts, 1);
    assert.equal(result.groups.find(value => value.label === 'Reduced, at least two candidates').count, 1);
    assert.equal(result.groups.find(value => value.label === 'Zero candidates').count, 1);
    assert.equal(result.groups.reduce((sum, value) => sum + value.count, 0), 2);
    const inconsistent = analytics.reductionSummary([a, { ...a, evidence: { candidate_count: 20, remaining_candidate_count: 19 } }]);
    assert.equal(inconsistent.groups.find(value => value.label === 'Unknown/inconsistent counts').count, 1);
    const conflictWithUnknownCounts = analytics.reductionSummary([row({ key_image: 'C', evidence: { conflicts: ['Mismatch'] } })]);
    assert.equal(conflictWithUnknownCounts.conflicts, 1);
    assert.equal(conflictWithUnknownCounts.groups.find(value => value.label === 'Unknown/inconsistent counts').count, 1);
});

test('provenance field coverage has explicit row and verified-row denominators', () => {
    const runs = new Map([['known', { metadata: { feature_version: 'v1', artifact_sha256: 'a'.repeat(64), working_source_sha256: 'b'.repeat(64) } }]]);
    const rows = [row({ run_id: 'known', scan_height: 0, verified: true, correct: true,
        verification_provenance: 'deterministic_resolution', verification_height: 2 }), row({ run_id: 'missing' })];
    const fields = analytics.provenanceSummary(rows, runs);
    assert.deepEqual(fields.find(value => value.label === 'Feature version recorded'), { label: 'Feature version recorded', known: 1, total: 2 });
    assert.equal(fields.at(-1).known, 1);
    assert.equal(fields.at(-1).total, 1);
    assert.equal(fields.find(value => value.label === 'Complete alternative scores').known, 0);
});

test('aggregate downloads exclude raw ring/output identities and neutralize formula cells', () => {
    const report = analytics.analyze(data([row({ key_image: 'DO_NOT_EXPORT_RING', predicted_amount: 'DO_NOT_EXPORT_AMOUNT',
        predicted_output_index: 'DO_NOT_EXPORT_INDEX', tx_hashes: ['DO_NOT_EXPORT_TX'] })]));
    const csv = analytics.aggregateCSV(report);
    assert.match(csv, /wilson95_low/);
    assert.match(csv, /selection/);
    for (const value of ['DO_NOT_EXPORT_RING', 'DO_NOT_EXPORT_AMOUNT', 'DO_NOT_EXPORT_INDEX', 'DO_NOT_EXPORT_TX']) {
        assert.equal(csv.includes(value), false);
        assert.equal(JSON.stringify(report).includes(value), false);
    }
    assert.equal(analytics.csvCell('=SUM(A1)'), '"\'=SUM(A1)"');
    assert.equal(analytics.csvCell('@formula'), '"\'@formula"');
    assert.equal(analytics.csvCell('  =SUM(A1)'), '"\'  =SUM(A1)"');
    assert.equal(analytics.csvCell('comma,"quote"'), '"comma,""quote"""');
});

test('empty and legacy exports yield explicit unavailable metrics instead of invented success', () => {
    for (const value of [{}, null, { prediction_browser: { rows: [null, 'bad'] } }]) {
        const report = analytics.analyze(value);
        assert.equal(report.scope.includedRecords, 0);
        assert.equal(report.scope.historicalRecords, null);
        assert.equal(report.retained.accuracy, null);
        assert.equal(report.retained.coverage, null);
        assert.equal(report.ambiguity.complete, 0);
    }
});

test('checked-in schema2 export reconciles analytics without promoting legacy candidate lists', () => {
    const snapshot = JSON.parse(fs.readFileSync(path.join(__dirname, '../docs/data.json'), 'utf8'));
    const report = analytics.analyze(snapshot, { threshold: 0 });
    const included = snapshot.prediction_browser.rows.length;
    assert.equal(report.scope.includedRecords, included);
    assert.equal(report.retained.total + report.filter.unknownScoresExcluded, included);
    assert.equal(report.retained.correct + report.retained.wrong + report.retained.pending + report.retained.unknown, report.retained.total);
    assert.equal(report.ambiguity.states.reduce((sum, value) => sum + value.count, 0), report.retained.total);
    const legacyRunIds = new Set(snapshot.prediction_browser.runs.filter(run => run.metadata?.origin === 'legacy_import').map(run => run.run_id));
    if (snapshot.prediction_browser.rows.every(value => legacyRunIds.has(value.run_id))) assert.equal(report.ambiguity.complete, 0);
});
