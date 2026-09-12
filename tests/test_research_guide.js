const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const guide=require('../docs/research-guide.js');
test('all audited model features have plain explanations and scope limits',()=>{
    const audit=JSON.parse(fs.readFileSync(require.resolve('../docs/feature-audit.json')));
    assert.deepEqual(Object.keys(guide.features).sort(),audit.features.map(f=>f.name).sort());
    for(const entry of Object.values(guide.features))assert.ok(entry[1].length>80);
    assert.match(guide.features.distance_from_tx[1],/different units/);
    assert.match(guide.features.reuse_rank_in_ring[1],/ties/);
    assert.match(guide.help['Smallest complete score gaps'],/every candidate/);
});
test('chain coverage uses stored block count and inclusive tip, preserving unknown and mismatched inputs',()=>{
    const data={summary:{blocks_scanned:25},generated_at:'2026-09-12T05:00:00Z'},live={chain_tip:{height:99,observed_at:'2026-09-12T05:01:00Z'}};
    assert.equal(guide.scanCoverage(data,live).percent,25);
    assert.equal(guide.scanCoverage({summary:{blocks_scanned:100}},live).percent,100);
    assert.equal(guide.scanCoverage({summary:{blocks_scanned:101}},live),null);
    assert.equal(guide.scanCoverage(data,null),null);
    assert.equal(guide.scanCoverage({summary:{blocks_scanned:-1}},live),null);
    assert.equal(guide.scanCoverage({summary:{blocks_scanned:0}},live).percent,0);
});
