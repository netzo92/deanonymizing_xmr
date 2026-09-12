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

function tieResult() {
    const comparison = (id, train, removed = 0) => ({id,label:id.replaceAll('_',' '),train_rings:train,test_rings:10,train_candidates:train*3,test_candidates:30,train_removed:removed,cutoff_height:id==='random'?null:40000,test_rings_with_reuse_ties:6,baseline:{correct:8,agreement:.8,top_tied_rings:0},equal_rank:{correct:9,agreement:.9,top_tied_rings:1},paired:{rings:10,both_correct:7,baseline_only_correct:1,variant_only_correct:2,neither_correct:0,net_correct_change:1,agreement_change:.1,changed_selected_outputs:3}});
    return {schema_version:1,experiment_id:'FA3',generated_at:'2026-09-12T06:00:00Z',source_revision:'a'.repeat(40),scope:{scan_start:0,scan_end:58900,metric:'retrospective_label_agreement',description:'Synthetic validation fixture, not measured evidence.'},comparisons:[comparison('random',40),comparison('chronological',40),comparison('chronological_purged',30,10)],limitations:['Synthetic fixture only.'],sources:[{label:'Protocol',path:'research/feature_ablation_protocol.json'}]};
}
test('equal reuse counts share average normalized positions, preserving the original untied ranks',()=>{
    assert.deepEqual(guide.reuseRanks([2,1,1,4]),{current:[2/3,0,1/3,1],equal:[2/3,1/6,1/6,1]});
    const untied=guide.reuseRanks([4,1,3,2]);assert.deepEqual(untied.current,untied.equal);
    assert.deepEqual(guide.reuseRanks([3,3,3,3]).equal,[.5,.5,.5,.5]);
    assert.deepEqual(guide.reuseRanks([9,9]).equal,[.5,.5]);
});
test('rank examples reject incomplete, noninteger or invalid reuse counts',()=>{
    for(const value of [[],[1],[1,0],[1,1.5],[1,NaN],Array(33).fill(1)])assert.throws(()=>guide.reuseRanks(value));
});
test('three FA3 cohorts retain exact paired counts and distinct agreement semantics',()=>{
    const data=tieResult();assert.equal(guide.reuseExperiment(data),data);
    const wrongMetric=tieResult();wrongMetric.scope.metric='forward_accuracy';assert.throws(()=>guide.reuseExperiment(wrongMetric),/scope/);
    const wrongSource=tieResult();wrongSource.sources[0].path='../private.db';assert.throws(()=>guide.reuseExperiment(wrongSource),/source/);
});
test('FA3 display rejects rates inconsistent with the heldout denominator',()=>{
    const wrong=tieResult();wrong.comparisons[0].equal_rank.agreement=.95;assert.throws(()=>guide.reuseExperiment(wrong),/rates/);
    const absent=tieResult();absent.comparisons[0].test_rings=0;assert.throws(()=>guide.reuseExperiment(absent),/denominator/);
    const missing=tieResult();missing.comparisons[0].baseline.correct=null;assert.throws(()=>guide.reuseExperiment(missing),/rates/);
});
test('FA3 paired partitions and changed predictions must reconcile',()=>{
    for(const [key,value] of [['both_correct',8],['net_correct_change',2],['agreement_change',.2],['changed_selected_outputs',2],['rings',11]]){
        const bad=tieResult();bad.comparisons[0].paired[key]=value;assert.throws(()=>guide.reuseExperiment(bad),/paired/);
    }
});
test('temporal and purged comparisons must keep the same heldout population',()=>{
    const changed=tieResult();changed.comparisons[2].test_candidates=31;assert.throws(()=>guide.reuseExperiment(changed),/same test/);
    const removed=tieResult();removed.comparisons[2].train_removed=9;assert.throws(()=>guide.reuseExperiment(removed),/same test/);
    const cutoff=tieResult();cutoff.comparisons[2].cutoff_height=41000;assert.throws(()=>guide.reuseExperiment(cutoff),/same test/);
    const duplicate=tieResult();duplicate.comparisons[2].id='random';assert.throws(()=>guide.reuseExperiment(duplicate),/cohorts/);
});
