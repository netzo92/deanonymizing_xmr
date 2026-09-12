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
        'Does the tie rule matter?': 'FA3 changes only how one reuse-rank feature handles equal counts. The bars compare agreement with stored labels within each fixed test population. This does not measure sender identification or forward accuracy.',
        'Explore the rank rule': 'These four invented outputs show how equal counts receive different index-ordered ranks today, or one shared average rank under the experimental rule. Choose an example and point to, tap or focus an output for its calculation.',
        'Cohort': 'A declared group of rings evaluated together. Here, each row defines its training/test split; compare the two models within that row because different rows can contain different populations.',
        'Train / test rings': 'Rings used to fit each model, followed by held-out rings used to compare its choices with stored labels. Candidate rows from a ring stay together. These counts are not numbers of independent people or wallets.',
        'Current correct': 'Current index-ordered rank model selections that match the stored deterministic label, divided by all test rings in this cohort. This is retrospective label agreement under analyzer assumptions.',
        'Equal-rank correct': 'Experimental equal-midrank model selections that match the stored deterministic label, divided by the same test rings. The deployed model has not adopted this rule.',
        'Newly correct / wrong': 'Paired changes relative to the current rule: first, rings that switch from a mismatching to a matching selection; second, rings that switch from matching to mismatching. Subtract the second count from the first for the net gain.',
        'Changed choices': 'Test rings where the two fitted models choose different output identities. Both choices can still disagree with the stored label, so a changed choice is not automatically a gain.',
        'Train removed': 'Earlier training rings excluded because their sampled component touches a later test output or transaction. Earlier unlabeled rings can form bridges. This offline filter does not prove full-graph independence or common ownership.',
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
    const TIE_COHORTS = ['random', 'chronological', 'chronological_purged'];
    function reuseRanks(counts) {
        if (!Array.isArray(counts) || counts.length < 2 || counts.length > 32 || counts.some(value => !Number.isSafeInteger(value) || value < 1)) throw Error('A rank example needs at least two positive integer reuse counts.');
        const ordered = counts.map((value,index) => ({value,index})).sort((a,b) => a.value - b.value || a.index - b.index);
        const current = Array(counts.length), equal = Array(counts.length);
        ordered.forEach((item, position) => { current[item.index] = position / (counts.length - 1); });
        for (let begin = 0; begin < ordered.length;) {
            let end = begin + 1; while (end < ordered.length && ordered[end].value === ordered[begin].value) end++;
            const rank = (begin + end - 1) / (2 * (counts.length - 1));
            for (let i = begin; i < end; i++) equal[ordered[i].index] = rank;
            begin = end;
        }
        return {current,equal};
    }
    function reuseExperiment(data) {
        const integer = value => Number.isSafeInteger(value) && value >= 0;
        const finite = value => typeof value === 'number' && Number.isFinite(value);
        const near = (a,b) => finite(a) && Math.abs(a-b) < 1e-10;
        if (data?.schema_version !== 1 || data.experiment_id !== 'FA3' || typeof data.generated_at !== 'string' || !Number.isFinite(Date.parse(data.generated_at)) || data.scope?.metric !== 'retrospective_label_agreement' || data.scope.scan_start !== 0 || data.scope.scan_end !== 58900 || typeof data.scope.description !== 'string') throw Error('Unsupported or missing FA3 research scope.');
        if (typeof data.source_revision !== 'string' || !/^[a-f0-9]{40}$/.test(data.source_revision) || !Array.isArray(data.comparisons) || data.comparisons.length !== 3 || new Set(data.comparisons.map(row => row.id)).size !== 3 || !data.comparisons.every(row => TIE_COHORTS.includes(row.id))) throw Error('The three registered FA3 cohorts are incomplete.');
        if (!Array.isArray(data.limitations) || data.limitations.some(value => typeof value !== 'string') || !Array.isArray(data.sources) || data.sources.some(item => typeof item.label !== 'string' || !source(item.path))) throw Error('Invalid FA3 limitations or source links.');
        for (const row of data.comparisons) {
            if (typeof row.label !== 'string' || !['train_rings','test_rings','train_candidates','test_candidates','train_removed','test_rings_with_reuse_ties'].every(key => integer(row[key])) || row.train_rings < 1 || row.test_rings < 1 || row.train_candidates < 2 * row.train_rings || row.test_candidates < 2 * row.test_rings || row.test_rings_with_reuse_ties > row.test_rings) throw Error('Invalid FA3 cohort denominator.');
            if (row.id === 'random' ? row.cutoff_height !== null || row.train_removed !== 0 : !integer(row.cutoff_height) || row.cutoff_height > data.scope.scan_end) throw Error('Invalid FA3 temporal cutoff.');
            for (const model of [row.baseline, row.equal_rank]) if (!model || !integer(model.correct) || model.correct > row.test_rings || !near(model.agreement, model.correct / row.test_rings) || !integer(model.top_tied_rings) || model.top_tied_rings > row.test_rings) throw Error('FA3 model rates do not reconcile with exact heldout counts.');
            const paired = row.paired;
            if (!paired || !['rings','both_correct','baseline_only_correct','variant_only_correct','neither_correct','changed_selected_outputs'].every(key => integer(paired[key])) || paired.rings !== row.test_rings || paired.both_correct + paired.baseline_only_correct + paired.variant_only_correct + paired.neither_correct !== paired.rings || paired.both_correct + paired.baseline_only_correct !== row.baseline.correct || paired.both_correct + paired.variant_only_correct !== row.equal_rank.correct || paired.net_correct_change !== row.equal_rank.correct - row.baseline.correct || !near(paired.agreement_change, paired.net_correct_change / paired.rings) || paired.changed_selected_outputs < paired.baseline_only_correct + paired.variant_only_correct || paired.changed_selected_outputs > paired.rings) throw Error('FA3 paired outcomes do not reconcile.');
        }
        const chronological = data.comparisons.find(row => row.id === 'chronological'), purged = data.comparisons.find(row => row.id === 'chronological_purged');
        if (chronological.train_removed !== 0 || chronological.cutoff_height !== purged.cutoff_height || chronological.train_rings !== purged.train_rings + purged.train_removed || ['test_rings','test_candidates','test_rings_with_reuse_ties'].some(key => chronological[key] !== purged[key]) || purged.train_candidates > chronological.train_candidates) throw Error('Chronological and purged cohorts must share the same test population.');
        return data;
    }
    function rankExample(host) {
        const scenarios = {mixed: {label:'Some equal counts',counts:[2,1,1,4]}, all: {label:'Every count is equal',counts:[3,3,3,3]}, none: {label:'No equal counts',counts:[4,1,3,2]}};
        const panel = el('div',null,'reuse-example');
        const intro = el('div',null,'reuse-example-intro');
        intro.append(el('h3','Explore the rank rule'),el('p','Illustrative: four invented outputs from one amount bucket, in ascending index order. These are not chain observations.'));
        const label = el('label','Reuse-count example'), choose = el('select'); choose.id='reuse-rank-scenario'; choose.setAttribute('aria-label','Choose illustrative reuse counts');
        for (const [key,scenario] of Object.entries(scenarios)) { const option=el('option',scenario.label); option.value=key; choose.append(option); }
        label.append(choose); intro.append(label);
        const chart = el('div',null,'reuse-example-chart');
        const legend = el('p','Blue: current index-ordered rank · Green: equal midrank','reuse-legend');
        const rows = el('div',null,'reuse-rank-rows'), description=el('p',null,'reuse-rank-description'); description.id='reuse-rank-description'; description.setAttribute('role','status');
        const buttons=[], currentBars=[], equalBars=[], captions=[]; let selected=1;
        const values=()=>scenarios[choose.value];
        function explain(index, focus=false) {
            selected=index; const counts=values().counts, ranks=reuseRanks(counts);
            buttons.forEach((button,i)=>{button.setAttribute('aria-pressed',String(i===index));});
            const tied=counts.filter(value=>value===counts[index]).length;
            description.textContent=`Output ${['A','B','C','D'][index]} (invented index ${(index+1)*10}) has reuse count ${counts[index]}. Current rank ${ranks.current[index].toFixed(3)}; equal midrank ${ranks.equal[index].toFixed(3)}. ${tied>1 ? `${tied} candidates share this count. The equal rule gives each their group's average normalized rank.` : 'This count has no ties, so both rules assign the same rank.'}`;
            if(focus)buttons[index].focus();
        }
        for(let i=0;i<4;i++) {
            const row=el('div',null,'reuse-rank-row'),button=el('button',null,'reuse-output');button.type='button';button.setAttribute('aria-describedby',description.id);buttons.push(button);
            const tracks=el('div',null,'reuse-rank-tracks'),a=el('div',null,'reuse-rank-track'),b=el('div',null,'reuse-rank-track');
            const barA=el('span',null,'reuse-rank-current'),barB=el('span',null,'reuse-rank-equal');currentBars.push(barA);equalBars.push(barB);a.append(barA);b.append(barB);tracks.append(a,b);tracks.setAttribute('aria-hidden','true');
            const caption=el('span',null,'reuse-rank-caption');captions.push(caption);row.append(button,tracks,caption);rows.append(row);
            button.addEventListener('mouseenter',()=>explain(i));button.addEventListener('focus',()=>explain(i));button.addEventListener('click',()=>explain(i));
            button.addEventListener('keydown',event=>{let index=i;if(event.key==='ArrowDown'||event.key==='ArrowRight')index=(i+1)%4;else if(event.key==='ArrowUp'||event.key==='ArrowLeft')index=(i+3)%4;else if(event.key==='Home')index=0;else if(event.key==='End')index=3;else return;event.preventDefault();explain(index,true);});
        }
        function update() { const counts=values().counts,ranks=reuseRanks(counts);for(let i=0;i<4;i++){buttons[i].textContent=`${['A','B','C','D'][i]} · count ${counts[i]}`;buttons[i].setAttribute('aria-label',`Output ${['A','B','C','D'][i]}, reuse count ${counts[i]}, current rank ${ranks.current[i].toFixed(3)}, equal rank ${ranks.equal[i].toFixed(3)}`);currentBars[i].style.width=`${100*ranks.current[i]}%`;equalBars[i].style.width=`${100*ranks.equal[i]}%`;captions[i].textContent=`${ranks.current[i].toFixed(3)} → ${ranks.equal[i].toFixed(3)}`;}explain(selected); }
        choose.addEventListener('change',update);chart.append(legend,rows,description,el('p','Midrank = average of the tied group’s zero-based positions, divided by (ring size − 1). When all counts tie, each candidate receives 0.5.','hint'));panel.append(intro,chart);host.append(panel);update();
    }
    function mountReuseTies() {
        const host=document.getElementById('reuse-tie-results');if(!host)return;
        rankExample(document.getElementById('reuse-rank-example'));
        const status=document.getElementById('reuse-tie-status');let payload=null;
        if(!root.TraceGroveLive){status.textContent='The refresh script is unavailable. Reload to retry; the rank example remains usable.';host.setAttribute('aria-busy','false');return;}
        const pct = value => `${(100*value).toFixed(2)}%`;
        function render(data) {
            const container=el('div');
            container.append(el('h3','Measured agreement within each cohort'),el('p',data.scope.description),el('p',`Heights ${data.scope.scan_start.toLocaleString()}–${data.scope.scan_end.toLocaleString()} · result exported ${date(data.generated_at)}. These are selectively labeled historical rings, not forward or modern-chain accuracy.`,'hint'));
            const bars=el('div',null,'reuse-result-bars');bars.setAttribute('role','img');bars.setAttribute('aria-label','Current and equal-rank label agreement within each heldout cohort. Exact counts and paired changes follow in the table.');
            const rows=TIE_COHORTS.map(id=>data.comparisons.find(row=>row.id===id));
            for(const row of rows){const group=el('div',null,'reuse-result-row');group.append(el('strong',row.label));for(const [name,model,cls] of [['Current',row.baseline,'reuse-rank-current'],['Equal ranks',row.equal_rank,'reuse-rank-equal']]){const line=el('div',null,'reuse-result-line'),track=el('span',null,'reuse-result-track'),fill=el('span',null,cls);fill.style.width=`${100*model.agreement}%`;track.append(fill);line.append(el('span',name),track,el('span',`${model.correct}/${row.test_rings} · ${pct(model.agreement)}`));group.append(line);}bars.append(group);}
            container.append(bars);
            const wrap=el('div',null,'reuse-results-table');wrap.tabIndex=0;wrap.dataset.viewKey='reuse-table';wrap.setAttribute('role','region');wrap.setAttribute('aria-label','Scrollable paired reuse-rank experiment results');
            const table=el('table'),head=el('thead'),header=el('tr');for(const text of ['Cohort','Train / test rings','Current correct','Equal-rank correct','Newly correct / wrong','Changed choices','Train removed']){const th=el('th',text);th.scope='col';header.append(th);}head.append(header);table.append(head);const body=el('tbody');
            for(const row of rows){const tr=el('tr');for(const text of [row.label,`${row.train_rings.toLocaleString()} / ${row.test_rings.toLocaleString()}`,`${row.baseline.correct} / ${row.test_rings}`,`${row.equal_rank.correct} / ${row.test_rings}`,`${row.paired.variant_only_correct} / ${row.paired.baseline_only_correct}`,row.paired.changed_selected_outputs,row.train_removed])tr.append(el('td',text));body.append(tr);}table.append(body);wrap.append(table);container.append(wrap);
            container.append(el('p','Comparisons are paired within a cohort: both models score the same test rings. Chronological and purged rows keep the same future test population; purging changes the training population. Differences between cohort rows do not establish a causal effect.','reuse-result-caveat'));
            const details=el('details');details.dataset.viewKey='reuse-cohort-details';const summary=el('summary','Cohort sizes, prediction ties and limitations');summary.dataset.viewKey='reuse-cohort-summary';details.append(summary);
            for(const row of rows)details.append(el('p',`${row.label}: ${row.train_candidates.toLocaleString()} training candidates; ${row.test_candidates.toLocaleString()} test candidates. ${row.test_rings_with_reuse_ties}/${row.test_rings} test rings contain equal reuse counts. Tied top model scores: current ${row.baseline.top_tied_rings}, equal rank ${row.equal_rank.top_tied_rings}.${row.cutoff_height===null?'':` Chronological cutoff: height ${row.cutoff_height.toLocaleString()}.`} Net agreement change: ${row.paired.net_correct_change>=0?'+':''}${row.paired.net_correct_change} rings (${row.paired.agreement_change>=0?'+':''}${(100*row.paired.agreement_change).toFixed(2)} percentage points).`));
            const limits=el('ul');data.limitations.forEach(value=>limits.append(el('li',value)));details.append(limits);container.append(details);
            const sources=el('p',null,'reuse-result-sources');data.sources.forEach(item=>sources.append(link(`${item.label} ↗`,source(item.path)),' '));sources.append(link(`Source commit ${data.source_revision.slice(0,8)} ↗`,`https://github.com/netzo92/deanonymizing_xmr/commit/${data.source_revision}`),link('Public result JSON ↗','reuse-tie-experiment.json'));container.append(sources);host.replaceChildren(container);
        }
        root.TraceGroveReuseTiePoller=root.TraceGroveLive.startPolling({load:()=>root.TraceGroveLive.fetchJSON('reuse-tie-experiment.json').then(reuseExperiment),onData:data=>{payload=data;const preserve=root.TraceGroveResearchActivity?.preserveView || ((element,callback)=>callback());preserve(host,()=>render(data));},onStatus:result=>{host.setAttribute('aria-busy','false');status.textContent=result.ok?`Published FA3 results checked ${date(result.checkedAt)} · checks every 60 seconds.`:`FA3 result check failed (${result.error}). ${payload?'Keeping the last valid results.':'No validated result snapshot is available here yet.'} The illustrative rank example remains usable; retrying every 60 seconds.`;}});
    }
    const api={features,help,scanCoverage,updateScan,reuseRanks,reuseExperiment};if(typeof module==='object'&&module.exports)module.exports=api;root.TraceGroveGuide=api;
    if(root.document)document.addEventListener('DOMContentLoaded',()=>{installHelp();mountConclusions();mountReuseTies();if(document.getElementById('knowledge-brain') && root.TraceGroveLive)root.TraceGroveLive.startPolling({load:()=>root.TraceGroveLive.fetchJSON('task-activity.json'),onData:data=>{noteHistory=data;annotateNotes();},onStatus() {}});});
})(typeof globalThis!=='undefined'?globalThis:this);
