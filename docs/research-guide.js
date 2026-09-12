/* Plain-language research interpretation, accessible help and dated findings. */
(function (root) {
    'use strict';
    const features = {
        output_age_rank: ['Output position rank', 'Position in the ring after sorting output indices: 0 is the lowest index and 1 the highest. It is an ordering proxy, not elapsed time.'],
        normalized_age: ['Relative output position', 'The candidate’s index minus the ring’s minimum index, divided by its index range. Despite its name, this is not age in blocks or seconds.'],
        ring_reuse_count: ['Output reuse, log scale', 'log(1 + the number of original rings containing this amount/index output), including this ring. The frozen graph can include later references.'],
        is_newest_member: ['Highest-index candidate', '1 for the largest output index in this ring, otherwise 0. It identifies an index position, not the actual spend.'],
        age_matches_decoy_distribution: ['Distance from an assumed decoy rank', 'Absolute distance between the output’s rank and a hard-coded rank of 0.75. This is an assumed proxy, not a measured match to the current wallet sampler.'],
        ring_size: ['Number of candidates', 'Number of candidates passed to feature extraction. Training uses original full rings; unresolved scoring uses surviving candidates. This is a context feature shared by the whole ring.'],
        gap_to_next: ['Gap to the next index', 'Distance to the next higher candidate index divided by the ring’s full index range. The final candidate gets 0. This measures index spacing, not time.'],
        gap_to_prev: ['Gap from the previous index', 'Distance from the next lower candidate index divided by the ring’s full index range. The first candidate gets 0.'],
        distance_from_tx: ['Block-height/index proxy', 'The referencing block height minus the output index, divided by block height. These quantities have different units; this feature must not be interpreted as measured output age.'],
        reuse_rank_in_ring: ['Relative reuse rank', 'Rank from least reused (0) to most reused (1). Equal reuse counts are ordered by output index in the current implementation, so ties also carry index-order information.'],
        is_min_reuse: ['Least-reused candidate flag', '1 when the candidate has the minimum reuse count in this ring. Several candidates may share this flag. It is a ranking heuristic, not a real-spend label.'],
        reuse_count_raw: ['Reuse relative to the ring maximum', 'The reuse count divided by the largest reuse count in the ring. Despite “raw” in the code name, the value is normalized.'],
        gap_isolation: ['Isolation between neighboring indices', 'The smaller neighboring normalized gap divided by the ring’s largest gap. Endpoints have a missing-side gap of 0, so their isolation is 0.'],
        is_max_gap_member: ['Largest-gap neighbor flag', '1 when either neighboring gap equals the largest gap in this ring. Multiple candidates can qualify. It does not establish a separate owner.'],
        gamma_decoy_log_likelihood: ['Approximate decoy log likelihood', 'A clipped log-likelihood proxy based on estimated age. It approximates part of a sampler, not the complete wallet selection process. In audited legacy rings its age fallback collapsed into a constant.'],
        gamma_recent_window: ['Estimated recent-window flag', '1 when estimated age falls in the implementation’s recent window. This was 1 for every audited legacy candidate; it is not evidence that all those outputs were recently created.'],
        gamma_age_surprisal: ['Scaled decoy surprisal', 'Negative approximate log likelihood divided by 50. It is a transformed proxy, not a calibrated probability; it was constant in the audited legacy sample.'],
        legacy_triangular_likelihood: ['Legacy index-density proxy', 'For nonzero amounts, (index + 1) divided by the largest observed index + 1 in that amount bucket; zero for amount-zero outputs. This uses observed coverage and is not a complete historical wallet replay.'],
        amount_bucket_progress: ['Position within observed amount bucket', 'The same normalized bucket position currently used by legacy_triangular_likelihood. These columns were exactly equal in the frozen audit.'],
        is_legacy_amount: ['Nonzero amount flag', '1 for nonzero amount buckets and 0 for amount-zero outputs. It was constant because the audited data were entirely legacy; that does not make it universally useless.'],
        decoy_likelihood_rank: ['Rank of the decoy proxy', 'Rank within the ring by the legacy position proxy or the amount-zero gamma proxy. Equal values retain index order. It is not a real-spend probability.'],
        is_most_decoy_like: ['Largest decoy-proxy flag', '1 for candidates tied for the largest approximate decoy score. “Decoy-like” describes the proxy, not a known decoy label.'],
        decoy_likelihood_gap: ['Gap below the largest decoy proxy', 'Largest approximate decoy score in the ring minus this candidate’s score. A small gap means similar proxy values, not calibrated certainty.'],
        decoy_likelihood_zscore: ['Standardized decoy proxy', 'The candidate’s approximate decoy value minus the ring mean, divided by population standard deviation; 0 if the deviation is effectively zero.']
    };
    const help = {
        'Smallest complete score gaps': 'For each scoring record with every candidate score available, subtract the second-highest score from the highest. This table sorts the smallest gaps first. A small gap means the model has two similarly ranked candidates; it is not an error probability.',
        'Resolved rings over export observations': 'Each point is a completed analysis export. The collector scans a block batch, analyzes it, then publishes totals. Growth can include newly imported singleton rings and revisions to earlier rings. A line between points is not a record of individual solve events.',
        'Recent pool snapshots': 'Each bar is one published poll of this node’s pool. Height is the number of returned transactions. An error marker means the count is unknown, not zero. The display shows a bounded recent window, not every poll since collection began.',
        'Expected agreement': 'Fraction of eligible rings where a rule agrees with the stored deterministic label. Tied choices receive fractional credit. These labels and the selected sample do not establish independent or forward accuracy.',
        'Feature importance': 'How the fitted model used a feature under its training procedure. Importance does not measure causal value, independent evidence, or improvement from adding that feature.',
        'Deterministic resolutions': 'Stored claims with confidence 1 and a non-negative analysis pass. They are conditional on the analyzer’s rules, data and provenance; they are distinct from independently verified spend labels.',
        'Score margin': 'Highest candidate score minus the second-highest, when both and all other candidates are available. Scores are model outputs and are not calibrated probabilities.',
        'Hypotheses': 'Proposed explanations or model guesses. A plausible score or a completed engineering task does not validate a scientific claim.',
        'Security eras': 'Protocol cohorts defined by pinned Monero upgrade heights. Evidence from one era does not automatically transfer to another.',
        'Pool observations': 'Repeated views of transactions visible in one node’s pool, followed by block-membership checks. Receipt and local observation times do not identify senders.',
        'Timeline & groups': 'Observed snapshot totals and direct shared-output or same-transaction relationships. Shared candidates do not establish common wallet ownership.',
        'Research brain': 'Linked Markdown notes with manually reviewed dates. Git-recorded edits and task transitions provide separate dated change histories.',
        'Predictions': 'Model-ranked candidate outputs. These remain hypotheses until separately supported; a large score alone is not proof.',
        'Evidence graph': 'Links between rings, outputs and recorded claims. Follow the stated edge meaning: overlap, co-occurrence and inference are different relationships.'
    };
    const normalize = value => String(value).trim().toLowerCase().replaceAll('_', ' ').replace(/\s+/g, ' ');
    const explanations = new Map([...Object.entries(help), ...Object.entries(features).flatMap(([key, value]) => [[key, value[1]], [value[0], value[1]]])].map(([key,value]) => [normalize(key), value]));
    const count = value => Number.isSafeInteger(value) && value >= 0;
    function scanCoverage(data, live) {
        const scanned = data?.summary?.blocks_scanned, height = live?.chain_tip?.height;
        if (!count(scanned) || !count(height) || height === Number.MAX_SAFE_INTEGER || scanned > height + 1) return null;
        return {scanned, total: height + 1, percent: scanned / (height + 1) * 100, analysisAt: data.generated_at, tipAt: live.chain_tip.observed_at};
    }
    const el = (tag, text, cls) => {const node = document.createElement(tag);if (text != null) node.textContent = text;if (cls) node.className = cls;return node;};
    const date = value => Number.isFinite(Date.parse(value)) ? new Date(value).toISOString().replace('T',' ').replace(/\.\d+Z$/, ' UTC') : 'Unknown';
    const source = path => typeof path === 'string' && /^[a-zA-Z0-9_./-]+$/.test(path) && !path.startsWith('/') && !path.split('/').includes('..') ? `https://github.com/netzo92/deanonymizing_xmr/blob/main/${path}` : null;
    function link(text, href) {const node = el('a',text);node.href = href;return node;}
    let analysis, current;
    function updateScan(data, live) {
        if (data) analysis = data;if (live) current = live;
        const host = document.getElementById('chain-scan-progress');if (!host) return;
        const value = scanCoverage(analysis, current);host.replaceChildren(el('h2','Chain blocks in published analysis'));
        if (!value) {host.append(el('p','Waiting for compatible analysis counts and an observed chain tip.'));return;}
        const bar = el('progress');bar.max = value.total;bar.value = value.scanned;bar.setAttribute('aria-label','Stored block count as a percentage of the observed chain');
        host.append(el('strong',`${value.percent.toFixed(2)}%`,'scan-percent'),bar,el('p',`${value.scanned.toLocaleString()} stored blocks / ${value.total.toLocaleString()} blocks through the observed tip.`),el('p',`Analysis exported ${date(value.analysisAt)} · tip observed ${date(value.tipAt)}. Counts update after each analysis batch. Stored heights do not by themselves prove every transaction body was collected.`,'hint'));
    }
    let noteHistory = null;
    function annotateNotes() {
        if (!noteHistory) return;
        for (const node of document.querySelectorAll('.brain-note-meta[data-note-id]')) {
            const events = (noteHistory.note_events || []).filter(event => event.note_id === node.dataset.noteId);
            const last = events.at(-1);if (!last || last.content_sha256 !== node.dataset.contentHash || node.dataset.historyCommit === last.commit) continue;
            node.querySelector('.recorded-note-dates')?.remove();
            const detail = el('span', ` · First recorded ${date(events[0].at)} · Last committed edit ${date(last.at)} · `, 'recorded-note-dates');
            detail.append(link('Dated changelog ↗', 'conclusions.html#changelog'));node.append(detail);node.dataset.historyCommit = last.commit;
        }
    }
    function installHelp() {
        const tooltip = el('div',null,'research-tooltip');tooltip.id = 'research-tooltip';tooltip.setAttribute('role','tooltip');tooltip.hidden = true;document.body.append(tooltip);
        let active = null, activeMessage = null;
        const close = () => {if(active) active.removeAttribute('aria-describedby');active=null;tooltip.hidden=true;};
        function show(button, message) {active=button;activeMessage=message;tooltip.textContent=message;tooltip.hidden=false;button.setAttribute('aria-describedby',tooltip.id);const box=button.getBoundingClientRect();tooltip.style.left=`${Math.max(8,Math.min(box.left,innerWidth-tooltip.offsetWidth-8))}px`;tooltip.style.top=`${Math.max(8,Math.min(box.bottom+8,innerHeight-tooltip.offsetHeight-8))}px`;}
        function scan() {
            annotateNotes();
            for(const node of document.querySelectorAll('h2,h3,th,td,caption,code,button,.metric-label,.workspace-nav a')) {
                if(node.dataset.helpAdded || node.classList.contains('research-help')) continue;
                const message=explanations.get(normalize(node.textContent));if(!message)continue;node.dataset.helpAdded='true';
                const button=el('button','?','research-help');button.type='button';button.setAttribute('aria-label',`Explain ${node.textContent.trim()}`);
                button.addEventListener('mouseenter',()=>show(button,message));button.addEventListener('mouseleave',()=>{if(document.activeElement!==button)close();});button.addEventListener('focus',()=>show(button,message));button.addEventListener('blur',close);button.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();show(button,message);});
                if(node.matches('button,code,a')) node.after(button);else node.append(' ',button);
            }
        }
        document.addEventListener('keydown',event=>{if(event.key==='Escape')close();});document.addEventListener('pointerdown',event=>{if(active && event.target!==active)close();});window.addEventListener('scroll',()=>{if(active && document.activeElement===active){const button=active,message=activeMessage;requestAnimationFrame(()=>{if(active===button)show(button,message);});}else close();},true);
        let scheduled=false;new MutationObserver(()=>{if(!scheduled){scheduled=true;requestAnimationFrame(()=>{scheduled=false;scan();});}}).observe(document.body,{childList:true,subtree:true});scan();
    }
    function mechanism(host) {
        const steps=[['Observed ring','A ring contains candidate outputs. The chain does not say which candidate was spent.'],['Conditional elimination','In this illustrative example, a separately established spend excludes candidate A under the analyzer’s assumptions.'],['Remaining candidates','B and C remain possible. Overlap alone does not establish which is real or who owns either output.'],['Model ranking','A model may rank B above C. That is a hypothesis; it does not turn the remaining candidate into proof.']];
        const drawing=el('div',null,'mechanism-drawing');drawing.setAttribute('aria-hidden','true');['Ring input','A','B','C'].forEach(value=>drawing.append(el('span',value)));const title=el('h3'),description=el('p'),next=el('button','Next step'),play=el('button','Play animation');next.type=play.type='button';let step=0,timer=null;
        const render=()=>{drawing.dataset.step=String(step);title.textContent=`${step+1}. ${steps[step][0]}`;description.textContent=steps[step][1];};next.onclick=()=>{step=(step+1)%steps.length;render();};play.onclick=()=>{if(timer){clearInterval(timer);timer=null;play.textContent='Play animation';}else{timer=setInterval(()=>{step=(step+1)%steps.length;render();},3500);play.textContent='Pause animation';}};
        host.append(el('h2','How the inference methods work'),el('p','Illustrative animation using invented candidates. This is not a replay of live discoveries.'),drawing,title,description,next,play);render();
    }
    function mountConclusions() {
        const host=document.getElementById('conclusion-results');if(!host)return;
        const state=document.getElementById('conclusion-status'),search=document.getElementById('conclusion-search'),filter=document.getElementById('conclusion-filter');let payload=null;
        function render() {
            if(!payload)return;host.replaceChildren();const q=normalize(search.value);
            for(const entry of payload.entries.filter(item=>(filter.value==='all'||item.execution_status===filter.value) && normalize(item.question+' '+item.measured_conclusion).includes(q)).sort((a,b)=>Math.max(0,...b.evidence.map(item=>Date.parse(item.observed_at)||0))-Math.max(0,...a.evidence.map(item=>Date.parse(item.observed_at)||0)))) {
                const card=el('article',null,'guide-card');card.append(el('p',`${entry.kind} · ${entry.execution_status.replaceAll('_',' ')} · ${entry.outcome.replaceAll('_',' ')}`,'eyebrow'),el('h3',entry.question),el('h4','What the evidence establishes'),el('p',entry.measured_conclusion),el('h4','Interpretation and limits'),el('p',entry.theoretical_conclusion));
                for(const evidence of entry.evidence)card.append(el('p',`Evidence captured ${date(evidence.observed_at)} · ${Object.entries(evidence.denominators).map(([key,value])=>`${key.replaceAll('_',' ')}: ${value.toLocaleString()}`).join(' · ')}`,'hint'));
                const ul=el('ul');entry.remaining.forEach(value=>ul.append(el('li',value)));card.append(el('h4','Next research / remaining limits'),ul);entry.sources.forEach(item=>{const url=source(item.path);if(url)card.append(link(item.label+' ↗',url),' ');});host.append(card);
            }
            if(!host.children.length)host.append(el('p','No matching experiments.'));
        }
        search.oninput=render;filter.onchange=render;
        root.TraceGroveLive.startPolling({load:()=>root.TraceGroveLive.fetchJSON('hypotheses.json').then(data=>{return root.TraceGroveResearchActivity.ledgerData(data);}),onData:data=>{payload=data;render();},onStatus:result=>{state.textContent=result.ok?'Experiment ledger checked · published changes checked every 60 seconds.':'Ledger check failed. Keeping available results and retrying.';}});
        root.TraceGroveLive.fetchJSON('feature-audit.json').then(audit=>{
            const container=document.getElementById('feature-explanations');
            Object.entries(features).forEach(([name,[label,explanation]])=>{const f=audit.features.find(value=>value.name===name),card=el('article',null,'guide-card');card.id='feature-'+name;card.append(el('h3',label),el('code',name),el('p',explanation));
                if(f)card.append(el('h4','What we measured'),el('p',`${f.finite_count.toLocaleString()} / ${f.candidate_count.toLocaleString()} finite candidate values; ${f.distinct_finite.toLocaleString()} distinct values. ${f.globally_constant?'Constant across this frozen sample.':'Varies across this frozen sample.'} Variation alone does not establish predictive value.`),el('p',`EA1 captured ${date(audit.generated_at)} · ${audit.sampling.sampled_rings.toLocaleString()} historical rings at heights ${audit.scope.scan_start}–${audit.scope.scan_end}.`,'hint'));
                const duplicates=audit.duplicate_feature_pairs.filter(pair=>pair.includes(name)).map(pair=>pair.find(other=>other!==name));if(duplicates.length)card.append(el('p',`Exactly equal in this sample to: ${duplicates.join(', ')}.`));
                card.append(el('p','Cannot determine: an actual spend, an address owner, calibrated probability, or modern-chain usefulness from this feature alone. Next: prediction-time evaluation, independently validated labels, and matched protocol cohorts.'));container.append(card);});
        }).catch(()=>document.getElementById('feature-explanations').append(el('p','Feature evidence could not be loaded. Reload to retry.')));
        root.TraceGroveLive.startPolling({load:()=>root.TraceGroveLive.fetchJSON('task-activity.json'),onData:data=>{
            const target=document.getElementById('research-changelog');target.replaceChildren(el('p','UTC times record committed edits in the available first-parent Git history. First recorded is not an independently known creation time. Removed notes and task closures are separate events.'));
            const events=[...(data.note_events||[]).map(event=>({...event,label:event.note_id})),...data.events.filter(event=>event.kind!=='first_recorded').map(event=>({...event,label:event.title}))].reverse();
            events.sort((a,b)=>Date.parse(b.at)-Date.parse(a.at));const list=el('ol');events.slice(0,100).forEach(event=>{const item=el('li');item.append(el('time',date(event.at)),` · ${event.kind.replaceAll('_',' ')} · `,link(event.label,`https://github.com/netzo92/deanonymizing_xmr/commit/${event.commit}`));list.append(item);});target.append(list,link('Full task activity and unread updates →','todos.html#activity'));
        },onStatus:result=>{document.getElementById('changelog-status').textContent=result.ok?'Published note and task history · latest 100 events shown.':'History check failed; keeping available entries.';}});
        mechanism(document.getElementById('inference-animation'));
    }
    const api={features,help,scanCoverage,updateScan};if(typeof module==='object'&&module.exports)module.exports=api;root.TraceGroveGuide=api;
    if(root.document)document.addEventListener('DOMContentLoaded',()=>{installHelp();mountConclusions();if(document.getElementById('knowledge-brain') && root.TraceGroveLive)root.TraceGroveLive.startPolling({load:()=>root.TraceGroveLive.fetchJSON('task-activity.json'),onData:data=>{noteHistory=data;annotateNotes();},onStatus() {}});});
})(typeof globalThis!=='undefined'?globalThis:this);
