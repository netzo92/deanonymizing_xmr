'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {freshness, createPoller} = require('../docs/live-feed.js');
test('freshness separates missing, recent, stale and clock mismatch', () => {
    const now = Date.parse('2026-09-12T02:00:00Z');
    assert.equal(freshness(null, now).state, 'unknown');
    assert.equal(freshness('2026-09-12T01:59:00Z', now).state, 'recent');
    assert.equal(freshness('2026-09-12T01:00:00Z', now).state, 'stale');
    assert.equal(freshness('2026-09-12T02:02:00Z', now).state, 'clock mismatch');
});
test('polling ignores unchanged snapshots and preserves last data across fetch failures', async () => {
    let source = {height: 1}, fail = false;
    const displayed = [], statuses = [];
    const p = createPoller({load: async () => {if(fail) throw Error('offline');return source;}, onData: d => displayed.push(d.height), onStatus: s => statuses.push(s)});
    await p.check();await p.check();fail=true;await p.check();fail=false;await p.check();source={height:2};await p.check();
    assert.deepEqual(displayed, [1,2]);
    assert.equal(statuses[2].hasSnapshot,true);
    assert.equal(statuses[2].error,'offline');
});
test('polling never overlaps requests and discards responses after stop', async () => {
    let release, calls=0, applied=0;
    const p=createPoller({load: () => {calls++;return new Promise(r=>release=r);}, onData: () => applied++});
    const first=p.check();assert.equal(await p.check(),false);assert.equal(calls,1);
    p.stop();release({height:1});await first;assert.equal(applied,0);await p.check();assert.equal(calls,1);
});
test('failed validation retries the same snapshot and hidden pages skip requests', async () => {
    let visible=false,calls=0,valid=false,applied=0;
    const p=createPoller({visible:()=>visible,load:async()=>{calls++;return {x:1};},onData:()=>{if(!valid)throw Error('bad schema');applied++;}});
    await p.check();assert.equal(calls,0);visible=true;await p.check();valid=true;await p.check();
    assert.equal(applied,1);assert.equal(calls,2);
});
