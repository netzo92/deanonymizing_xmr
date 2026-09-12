'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const api = require('../docs/pool-view.js');
const {createPoller} = require('../docs/live-feed.js');

function snapshot() {
    return {schema_version: 1, state: 'observing', generated_at: '2026-09-12T04:00:00Z', last_success_at: '2026-09-12T04:00:00Z', source: {mode: 'public_rpc', synchronized: true, restricted: true, bootstrap: false}, current: {transaction_count: 10, reported_pool_count: 11, complete: true, tracking_complete: true, untracked_due_limit: 0, fee_atomic_total: '18446744073709551615', weight_total: '79120', receive_time_known: 0, receive_time_unknown: 10}, coverage: {polls_total: 3, successful_polls: 2, failed_polls: 1, initial_window_left_truncated: true}, outcomes: {tracked_transactions: 20, pending: 10, disappeared: 3, confirmed: 5, censored: 2, cold_start_transactions: 8, followup_censored_transactions: 6, observed_confirmations: 5, confirmed_block_transactions: 40, confirmations_seen_before_block: 3, confirmation_delay: {sample_count: 2, median_seconds: 120, p90_seconds: 168, excluded_count: 3, bins: [{label: '< 1 minute', count: 0}, {label: '1–5 minutes', count: 2}], basis: 'Same-session monotonic observation to block detection'}}, poll_history: [{started_at: '2026-09-12T03:58:00Z', state: 'observing', pool_count: 8, pool_complete: true}, {started_at: '2026-09-12T03:59:00Z', state: 'error', pool_count: null, pool_complete: false, error_type: 'RPCError'}, {started_at: '2026-09-12T04:00:00Z', state: 'observing', pool_count: 10, pool_complete: true}], gaps: [], limits: {interval_seconds: 60, confirmations: 2, retention_seconds: 86400}};
}

test('large atomic fees retain all digits and invalid or missing numbers remain unknown', () => {
    assert.equal(api.formatXMR('18446744073709551615'), '18,446,744.073709551615');
    assert.equal(api.formatInteger('18446744073709551615'), '18,446,744,073,709,551,615');
    assert.equal(api.formatXMR('1'), '0.000000000001');
    assert.equal(api.formatXMR('0'), '0');
    for (const value of [undefined, null, -1, '1e12', '0.1', Number.MAX_SAFE_INTEGER + 1]) assert.equal(api.formatXMR(value), 'Unknown');
});

test('fee density handles zero weight and small positive fees without pretending they are zero', () => {
    assert.equal(api.feeDensity('10', '3'), '3.33');
    assert.equal(api.feeDensity('1', '1000'), '<0.01');
    assert.equal(api.feeDensity('0', '1000'), '0.00');
    assert.equal(api.feeDensity('1', '0'), null);
    assert.equal(api.feeDensity(null, '100'), null);
});

test('source mode does not turn public RPC or unknown sync status into independent validation', () => {
    const publicSource = api.sourceSummary({mode: 'public_rpc', synchronized: true});
    assert.equal(publicSource.privateNode, false);assert.match(publicSource.interpretation, /does not independently validate/);
    assert.equal(api.sourceSummary({mode: 'private_node', synchronized: false}).sync, 'Node is synchronizing');
    assert.equal(api.sourceSummary({mode: 'private_node', synchronized: true, bootstrap: true}).sync, 'Bootstrap data in use');
    assert.equal(api.sourceSummary({mode: 'private_node', synchronized: null}).sync, 'Synchronization unknown');
    assert.equal(api.sourceSummary({mode: 'other'}).label, 'Observation source not established');
});

test('failed poll markers are not successful empty pools; partial and unknown flags stay distinct', () => {
    assert.equal(api.pollKind({state: 'error', pool_complete: false, pool_count: null}), 'failed');
    assert.equal(api.pollKind({state: 'error', pool_complete: true, pool_count: 0}), 'followup_failed');
    assert.equal(api.pollKind({state: 'observing', pool_complete: false, pool_count: 8}), 'partial');
    assert.equal(api.pollKind({state: 'observing', pool_complete: true, pool_count: 0}), 'complete');
    assert.equal(api.pollKind({state: 'observing', pool_count: 8}), 'unknown');
});

test('cold start, tracking limits, source failures and missing pool completeness are explicit', () => {
    const data = snapshot();data.state = 'warming_up';data.current.tracking_complete = false;data.current.untracked_due_limit = 4;
    const result = api.derive(data);
    assert.ok(result.notices.some(note => note.includes('warming up')));
    assert.ok(result.notices.some(note => note.includes('4 returned entries')));
    assert.ok(result.notices.some(note => note.includes('partway through chain history')));
    data.current = null;data.state = 'error';data.error = {message: 'RPC unavailable'};
    const failure = api.derive(data);assert.ok(failure.notices.some(note => note.includes('RPC unavailable')));assert.ok(failure.notices.some(note => note.includes('completeness has not been established')));
});

test('no confirmations produces an empty measured cohort, not a zero delay', () => {
    const result = api.delaySummary({sample_count: 0, median_seconds: null, p90_seconds: null, bins: [], excluded_count: 0});
    assert.equal(result.state, 'empty');assert.equal(api.duration(result.median), 'Unknown');
    assert.equal(api.delaySummary(null).state, 'unknown');
    assert.equal(api.delaySummary({sample_count: 4, bins: [{label: '1–5m', count: 3}]}).state, 'unknown');
});

test('observed delay bins reconcile with their own sample, preserving excluded/censored populations', () => {
    const data = snapshot(), result = api.derive(data);
    assert.equal(result.delay.state, 'measured');assert.equal(result.delay.sampleCount, 2);assert.equal(result.delay.excludedCount, 3);
    assert.equal(result.outcomes.censored, 2);assert.equal(result.outcomes.disappeared, 3);
    assert.equal(result.outcomes.confirmed, 5);assert.equal(result.outcomes.confirmed_block_transactions, 40);
    assert.equal(api.duration(120), '2m 0s');assert.equal(api.duration(0), '0.0 s');
});

test('outcome states must reconcile but overlapping cold-start and interrupted cohorts are not added', () => {
    const good = snapshot();assert.equal(api.validateSnapshot(good), good);
    for (const mutate of [d => d.outcomes.pending++, d => d.outcomes.cold_start_transactions = 21, d => d.outcomes.confirmations_seen_before_block = 6, d => d.outcomes.observed_confirmations = 41, d => d.current.weight_total = '2.5', d => d.current.transaction_count = '10']) {
        const data = snapshot();mutate(data);assert.throws(() => api.validateSnapshot(data));
    }
    const partial = {schema_version: 1, state: 'paused', current: null, outcomes: null};assert.equal(api.validateSnapshot(partial), partial);
});

test('receipt availability, tracking limits, poll totals and delay eligibility reject contradictory exports', () => {
    for (const mutate of [d => d.current.receive_time_known++, d => d.current.untracked_due_limit = 11, d => d.coverage.failed_polls++, d => d.outcomes.confirmation_delay.excluded_count++, d => d.outcomes.confirmation_delay.bins[1].count--, d => d.outcomes.confirmation_delay.median_seconds = null, d => d.outcomes.confirmation_delay.p90_seconds = -1, d => d.outcomes.confirmation_delay.p90_seconds = 100]) {
        const data = snapshot();mutate(data);assert.throws(() => api.validateSnapshot(data));
    }
    const warming = snapshot();warming.outcomes.confirmation_delay = {sample_count: 0, excluded_count: 5, bins: [], median_seconds: null, p90_seconds: null};
    assert.equal(api.validateSnapshot(warming), warming);
});

test('shared poller keeps lastgood observations through transport and malformed-data failures, then recovers', async () => {
    let request = snapshot(), rendered = null, status;
    const poller = createPoller({load: async () => {if (request instanceof Error) throw request;return api.validateSnapshot(request);}, onData: data => {rendered = data;}, onStatus: value => {status = value;}});
    await poller.check();const first = rendered;assert.equal(status.ok, true);
    request = Error('HTTP 503');await poller.check();assert.equal(rendered, first);assert.equal(status.hasSnapshot, true);
    request = {schema_version: 99};await poller.check();assert.equal(rendered, first);assert.equal(status.ok, false);
    request = snapshot();request.outcomes.pending = 9;request.outcomes.disappeared = 4;await poller.check();assert.equal(rendered.outcomes.disappeared, 4);assert.equal(status.ok, true);
});

test('pool page uses only aggregate export and links all three prospective research criteria', () => {
    const fs = require('node:fs'), html = fs.readFileSync(require.resolve('../docs/pool.html'), 'utf8');
    for (const task of ['PN1', 'PN2', 'PN3']) assert.ok(html.includes(`task=${task}#tasks`));
    assert.ok(html.includes('pool-observations.json'));
    assert.ok(!html.includes('data.json') && !html.includes('Chart.js'));
});
