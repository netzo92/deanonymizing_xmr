'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {activityData, ledgerData, filterEvents, filterHypotheses, eventKey, unseenEvents, readSeen, writeSeen, taskDates, sourceURL} = require('../docs/task-activity-view.js');
const revision = 'a'.repeat(40), otherRevision = 'b'.repeat(40);
function event(kind = 'first_recorded', extra = {}) { return {task_id:'brain/research/test.md#T1', note_id:'brain/research/test.md', title:'T1 — Test a scoped claim', code:'T1', commit:revision, at:'2026-09-11T12:00:00Z', status:'open', kind, ...extra}; }
function activity() { const first = event(); return {schema_version:1, content_sha256:'c'.repeat(64), tasks:[{id:first.task_id, note_id:first.note_id, title:first.title, status:'open', first_recorded:{commit:revision, at:first.at, status:'open'}, events:[first]}], events:[first]}; }
function entry(extra = {}) { return {id:'test', kind:'hypothesis', question:'Does age improve forward accuracy?', execution_status:'completed', outcome:'inconclusive', theoretical_conclusion:'Variation could carry signal.', measured_conclusion:'No held-out comparison is available.', sources:[], evidence:[], remaining:['Run forward evaluation.'], task_refs:[{code:'T1', title:'Age experiment'}], ...extra}; }
test('first-recorded checked tasks never become fabricated closure dates', () => {
    const initial = activity().tasks[0]; initial.status = 'completed'; initial.first_recorded.status = 'completed'; initial.events[0].status = 'completed';
    const dates = taskDates(initial);
    assert.match(dates, /already checked/); assert.match(dates, /Closure date not observed/); assert.doesNotMatch(dates, /Last recorded closure/);
    initial.events.push(event('closed', {commit:otherRevision, at:'2026-09-12T12:00:00Z', status:'completed'}));
    initial.events.push(event('reopened', {commit:'c'.repeat(40), at:'2026-09-13T12:00:00Z'}));
    initial.pending_change = true;
    assert.match(taskDates(initial), /Last recorded closure 2026-09-12/); assert.match(taskDates(initial), /Last reopened 2026-09-13/); assert.match(taskDates(initial), /no event time assigned/);
});
test('activity validator rejects duplicate, unknown and undated committed events', () => {
    assert.equal(activityData(activity()).events.length, 1);
    const duplicate = activity(); duplicate.events.push({...duplicate.events[0]}); assert.throws(() => activityData(duplicate), /Duplicate/);
    const unknown = activity(); unknown.events[0].task_id = 'unknown'; assert.throws(() => activityData(unknown), /Invalid/);
    const undated = activity(); undated.events[0].at = null; assert.throws(() => activityData(undated), /Invalid/);
    const unsafe = activity(); unsafe.events[0].note_id = '../private'; assert.throws(() => activityData(unsafe), /Invalid/);
});
test('committed closures, removals and first appearances compose with exact event filters and search', () => {
    const events = [event(), event('closed', {commit:otherRevision, status:'completed'}), event('removed', {commit:'c'.repeat(40), status:'removed'})];
    assert.equal(filterEvents(events, {kind:'closed', query:'T1'}).length, 1);
    assert.equal(filterEvents(events, {kind:'closed', query:'other'}).length, 0);
    assert.equal(filterEvents(events, {query:'test.md'}).length, 3);
    assert.equal(filterEvents(events, {kind:'removed'})[0].status, 'removed');
});
test('first visits set a baseline and later commit events remain unread until acknowledged', () => {
    const old = event(), next = event('closed', {commit:otherRevision, status:'completed'});
    assert.deepEqual(unseenEvents([old], null), []);
    const seen = new Set([eventKey(old)]);
    assert.deepEqual(unseenEvents([old, next], seen), [next]);
    assert.deepEqual(unseenEvents([old, next], new Set([eventKey(old), eventKey(next)])), []);
    const values = new Map(), storage = {getItem:key => values.get(key) ?? null, setItem:(key,value) => values.set(key,value)};
    assert.equal(readSeen(storage), null); assert.equal(writeSeen(storage, [old]), true); assert.deepEqual(readSeen(storage), seen);
});
test('unavailable or malformed browser storage cannot stop the feed', () => {
    const denied = {getItem(){throw Error('denied');}, setItem(){throw Error('denied');}};
    assert.equal(readSeen(denied), null); assert.equal(writeSeen(denied, []), false);
    assert.equal(readSeen({getItem:() => '{bad'}), null); assert.equal(readSeen({getItem:() => '[12]'}), null);
});
test('execution completion and scientific verdict are separate and independently filterable', () => {
    const entries = [entry(), entry({id:'unfinished', execution_status:'not_started', outcome:'untested'}), entry({id:'display', kind:'engineering', execution_status:'completed', outcome:'not_applicable'})];
    assert.equal(ledgerData({schema_version:1, entries}).entries.length, 3);
    assert.deepEqual(filterHypotheses(entries, {execution:'completed', outcome:'inconclusive'}).map(item => item.id), ['test']);
    assert.deepEqual(filterHypotheses(entries, {kind:'engineering'}).map(item => item.id), ['display']);
    assert.equal(filterHypotheses(entries, {query:'T1'}).length, 3);
    assert.throws(() => ledgerData({schema_version:1, entries:[entry({kind:'engineering', outcome:'supported'})]}), /scientific verdict/);
});
test('all curated research ledger entries validate and preserve unsupported questions', () => {
    const data = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../research/hypotheses.json')));
    assert.equal(ledgerData(data), data);
    assert.ok(filterHypotheses(data.entries, {kind:'hypothesis', outcome:'untested'}).length > 0);
    assert.ok(filterHypotheses(data.entries, {kind:'measurement', outcome:'supported'}).length > 0);
    assert.equal(data.entries.filter(item => item.kind === 'engineering' && item.outcome !== 'not_applicable').length, 0);
});
test('source URLs stay in the repository and commit selection is validated', () => {
    assert.equal(sourceURL('brain/research/test.md', revision), `https://github.com/netzo92/deanonymizing_xmr/blob/${revision}/brain/research/test.md`);
    for (const value of ['../private.db','javascript:alert(1)','//example.com','/etc/passwd']) assert.equal(sourceURL(value), null);
    assert.match(sourceURL('brain/index.md', 'bad'), /\/main\//);
});
