const test = require('node:test');
const assert = require('node:assert/strict');
const {quality, withinRate, selectFeatures, validate, csv} = require('../docs/feature-audit.js');
const feature = overrides => ({name: 'ring_size', candidate_count: 4, finite_count: 4,
    missing_count: 0, nonfinite_count: 0, nonnumeric_count: 0, distinct_finite: 2,
    population_variance: .25, globally_constant: false,
    within_ring: {rings: 2, eligible_rings: 2, varied_rings: 0, constant_rings: 2, incomplete_rings: 0}, ...overrides});

test('global variation and within-ring constancy describe different populations', () => {
    const value = feature();
    assert.equal(quality(value), 'varying');
    assert.equal(withinRate(value), 0);
    assert.equal(withinRate(feature({within_ring: {eligible_rings: 0, varied_rings: 0}})), null);
    assert.equal(withinRate(feature({within_ring: {eligible_rings: 2, varied_rings: 3}})), null);
    assert.equal(withinRate(feature({within_ring: {eligible_rings: .5, varied_rings: .25}})), null);
    assert.equal(withinRate(feature({within_ring: {eligible_rings: 2, varied_rings: .5}})), null);
    assert.equal(withinRate(feature({within_ring: {eligible_rings: Number.MAX_SAFE_INTEGER + 1, varied_rings: 1}})), null);
});
test('missing and nonfinite values cannot appear as healthy constant features', () => {
    assert.equal(quality(feature({globally_constant: true, distinct_finite: 1})), 'constant');
    assert.equal(quality(feature({globally_constant: true, missing_count: 1, finite_count: 3})), 'incomplete');
    assert.equal(quality(feature({nonfinite_count: 1, finite_count: 3})), 'incomplete');
    assert.equal(quality(feature({nonnumeric_count: 1, finite_count: 3})), 'incomplete');
    assert.equal(quality(feature({finite_count: 0})), 'unavailable');
});
test('absent, inconsistent, and invalid count metadata cannot look like complete columns', () => {
    assert.equal(quality({name: 'partial', finite_count: 4, candidate_count: 5, globally_constant: true}), 'unavailable');
    for (const overrides of [
        {missing_count: undefined}, {nonfinite_count: null}, {nonnumeric_count: '0'},
        {candidate_count: 5}, {finite_count: 5}, {missing_count: -1}, {finite_count: 3.5},
        {globally_constant: true, distinct_finite: 2}, {globally_constant: false, distinct_finite: 1},
        {distinct_finite: 5}, {distinct_finite: 0}, {distinct_finite: null},
    ]) assert.equal(quality(feature(overrides)), 'unavailable', JSON.stringify(overrides));
});
test('feature search combines readable names and explicit variation filters', () => {
    const values = [feature(), feature({name: 'gamma_recent_window', globally_constant: true, distinct_finite: 1})];
    assert.equal(selectFeatures(values, 'GAMMA RECENT', 'constant').length, 1);
    assert.equal(selectFeatures(values, 'ring_size', 'constant').length, 0);
    assert.equal(selectFeatures(values, '', 'all').length, 2);
});
test('audit schema and unique feature names are required', () => {
    const data = {schema_version: 1, experiment_id: 'EA1', features: [feature()]};
    assert.equal(validate(data), data);
    assert.throws(() => validate({...data, schema_version: 2}));
    assert.throws(() => validate({...data, features: [feature(), feature()]}));
});
test('CSV keeps scope, denominators, missing cells, and formula-safe feature names', () => {
    const data = {experiment_id: 'EA1', scope: {scan_start: 0, scan_end: 100},
        provenance: {feature_version: 'test-v1'}, features: [feature({name: '=HYPERLINK("bad")', population_variance: null})]};
    const result = csv(data);
    assert.ok(result.includes('All sampled cohorts; candidate-weighted'));
    assert.ok(result.includes('within_ring_eligible'));
    assert.ok(result.includes("'=HYPERLINK"));
    assert.ok(result.includes('"2","","0","2"'));
});
