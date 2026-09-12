/* Protocol milestones and carefully scoped, independently refreshed evidence. */
(function (root, factory) {
    const api = factory(root);
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {
        root.TraceGroveEras = api;
        const host = document.getElementById('era-root');
        if (host) api.page = api.mount(host);
    }
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
    'use strict';
    const list = value => Array.isArray(value) ? value : [];
    const count = value => Number.isSafeInteger(value) && value >= 0;
    const fmt = value => count(value) ? value.toLocaleString() : 'Unknown';
    const has = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
    const FIELDS = ['blocks_scanned', 'transactions', 'total_rings', 'original_singleton_rings', 'original_multimember_rings', 'deterministic_resolutions', 'deterministic_singleton_resolutions', 'deterministic_multimember_resolutions', 'hypothesis_resolutions', 'unresolved_reduced', 'unresolved_unchanged', 'conflict_rings'];
    const URLS = {manifest: 'protocol-eras.json', historical: 'data.json', live: 'live-observations.json', frozen: 'research-progress.json'};
    const LABELS = {manifest: 'Protocol reference', historical: 'Historical analysis', live: 'Current observations', frozen: 'Frozen studies'};
    function validateManifest(data) {
        if (data?.schema_version !== 1 || data.network !== 'mainnet' || !Array.isArray(data.eras) || !data.eras.length) throw Error('Unsupported protocol manifest');
        const ids = new Set(), versions = new Set(); let previousEnd = -1;
        data.eras.forEach((era, i) => {
            if (!era || typeof era.id !== 'string' || !/^[a-z0-9_]+$/.test(era.id) || ids.has(era.id) || typeof era.label !== 'string' || !count(era.start_height) || era.start_height !== previousEnd + 1 || !(era.end_height === null && i === data.eras.length - 1 || count(era.end_height) && era.end_height >= era.start_height && i < data.eras.length - 1) || !Array.isArray(era.versions) || !era.versions.length || !era.versions.every(version => count(version) && version >= 1 && version <= 255 && !versions.has(version)) || new Set(era.versions).size !== era.versions.length) throw Error('Invalid era boundaries or block versions');
            ids.add(era.id); previousEnd = era.end_height === null ? Infinity : era.end_height;
            era.versions.forEach(version => versions.add(version));
        });
        return data;
    }
    function eraAtHeight(manifest, height) {
        if (!count(height)) return null;
        return list(manifest?.eras).find(era => height >= era.start_height && (era.end_height === null || height <= era.end_height)) || null;
    }
    function containedEra(manifest, scope) {
        if (!count(scope?.scan_start) || !count(scope?.scan_end) || scope.scan_end < scope.scan_start) return null;
        const first = eraAtHeight(manifest, scope.scan_start), last = eraAtHeight(manifest, scope.scan_end);
        return first && first.id === last?.id ? first : null;
    }
    function unknown(reason) { return {state: 'unknown', reason}; }
    function validRowCounts(row) {
        if (!row || !count(row.total_rings)) return false;
        if (FIELDS.some(key => has(row, key) && row[key] !== null && !count(row[key]))) return false;
        const bounded = FIELDS.filter(key => !['blocks_scanned', 'transactions', 'total_rings'].includes(key));
        if (bounded.some(key => count(row[key]) && row[key] > row.total_rings)) return false;
        const sums = [['original_singleton_rings', 'original_multimember_rings', 'total_rings'], ['deterministic_singleton_resolutions', 'deterministic_multimember_resolutions', 'deterministic_resolutions'], ['deterministic_resolutions', 'hypothesis_resolutions', 'unresolved_reduced', 'unresolved_unchanged', 'total_rings']];
        if (sums.some(keys => keys.every(key => count(row[key])) && keys.slice(0, -1).reduce((sum,key) => sum + row[key], 0) !== row[keys.at(-1)])) return false;
        return ![['deterministic_singleton_resolutions', 'original_singleton_rings'], ['deterministic_multimember_resolutions', 'original_multimember_rings']].some(([a,b]) => count(row[a]) && count(row[b]) && row[a] > row[b]);
    }
    function historicalEvidence(data, manifest, era) {
        if (!data) return unknown('The historical export is unavailable.');
        if (has(data, 'protocol_eras')) {
            const aggregate = data.protocol_eras;
            if (aggregate?.schema_version !== 1 || aggregate.status !== 'available' || aggregate.network !== 'mainnet' || aggregate.classification_basis !== 'mainnet_height_inferred') return unknown('Per-era analysis is unavailable or uses an unsupported classification in this export.');
            if (aggregate.reconciliation?.matches_summary !== true) return unknown('Per-era counts have not reconciled with the historical summary.');
            const rows = list(aggregate.rows);
            if (data.scope?.dataset_id && aggregate.dataset_id && data.scope.dataset_id !== aggregate.dataset_id) return unknown('The per-era aggregate and historical snapshot identify different datasets.');
            if (!rows.every(validRowCounts) || FIELDS.some(key => count(data.summary?.[key]) && rows.every(row => count(row[key])) && rows.reduce((sum,row) => sum + row[key], 0) !== data.summary[key])) return unknown('The per-era counts contain inconsistent evidence categories or denominators.');
            const mappingMatches = list(manifest.eras).every(item => {
                const matching = rows.filter(row => row.id === item.id);
                return matching.length === 1 && matching[0].start_height === item.start_height && matching[0].end_height === item.end_height && JSON.stringify(matching[0].versions) === JSON.stringify(item.versions);
            });
            if (!mappingMatches) return unknown('The export and protocol reference use different or incomplete era boundaries.');
            const row = rows.find(item => item.id === era.id);
            if (!count(row.total_rings) || !count(row.blocks_scanned)) return unknown('This era is missing its ring or block denominator.');
            const metrics = Object.fromEntries(FIELDS.map(key => [key, count(row[key]) ? row[key] : null]));
            return {state: row.total_rings === 0 && row.blocks_scanned === 0 ? 'no_coverage' : 'covered', metrics, basis: 'Exact per-era aggregate · classified by mainnet height', inferred: true, method: 'aggregate', reason: 'No historical blocks or rings from this era are in this export.'};
        }
        const only = containedEra(manifest, data.scope);
        if (!only) return unknown('This export has no exact era counts. Its summary cannot be divided across protocol eras.');
        if (only.id !== era.id) return {state: 'no_coverage', reason: 'The complete exported scan range belongs to a different era.'};
        if (!count(data.summary?.total_rings)) return unknown('The historical ring denominator is missing.');
        const metrics = Object.fromEntries(FIELDS.map(key => [key, count(data.summary[key]) ? data.summary[key] : null]));
        return {state: 'covered', metrics, basis: 'Single-era scan-range fallback', inferred: true, method: 'single_era_fallback', assumption: 'Assumes this explicitly bounded scan is mainnet. The entire scan range fits this era; no totals are prorated.'};
    }
    function classifyBlock(block, manifest) {
        const byHeight = eraAtHeight(manifest, block?.height);
        if (!count(block?.height)) return {era: null, basis: 'unknown'};
        const version = block.major_version ?? block.block_major_version;
        if (version !== undefined && version !== null) {
            const byVersion = count(version) ? list(manifest.eras).find(era => era.versions.includes(version)) : null;
            return {era: byVersion || null, basis: 'recorded_version', mismatch: Boolean(byVersion && byHeight && byVersion.id !== byHeight.id)};
        }
        return {era: byHeight, basis: 'mainnet_height_inferred', mismatch: false};
    }
    function liveEvidence(data, manifest, era) {
        if (!data || data.schema_version !== 1 || !Array.isArray(data.blocks)) return unknown('The recent-block observer export is unavailable.');
        const seen = new Set(), selected = []; let unclassified = 0, duplicates = 0;
        data.blocks.forEach(block => {
            if (count(block?.height) && seen.has(block.height)) { duplicates++; return; }
            if (count(block?.height)) seen.add(block.height);
            const classification = classifyBlock(block, manifest);
            if (!classification.era) unclassified++;
            else if (classification.era.id === era.id) selected.push({block, classification});
        });
        if (!selected.length && (data.state === 'error' || data.error)) return {...unknown('The observer reports an error and has no retained sample for this era. Coverage is not established.'), unclassified, duplicates};
        if (!selected.length) return {state: 'no_coverage', reason: 'No retained recent-block observations fall in this era.', unclassified, duplicates};
        const complete = selected.filter(({block}) => count(block.ring_input_count) && block.detail_status === 'complete');
        const transactionKnown = selected.filter(({block}) => count(block.transaction_count));
        const sum = (values, key) => values.reduce((n, {block}) => n + block[key], 0);
        const sizes = new Set();
        complete.forEach(({block}) => Object.entries(block.ring_size_distribution || {}).forEach(([size, n]) => {if (/^\d+$/.test(size) && count(n) && n > 0) sizes.add(Number(size));}));
        const heights = selected.map(({block}) => block.height);
        return {state: 'covered', blocks: selected.length, transactions: transactionKnown.length === selected.length ? sum(selected, 'transaction_count') : null, countedInputs: complete.length ? sum(complete, 'ring_input_count') : null, completeBlocks: complete.length, ringSizes: [...sizes].sort((a,b) => a-b), firstHeight: Math.min(...heights), lastHeight: Math.max(...heights), basis: selected.every(item => item.classification.basis === 'recorded_version') ? 'Recorded block major versions' : selected.every(item => item.classification.basis === 'mainnet_height_inferred') ? 'Inferred from mainnet height; versions not recorded' : 'Mixed recorded versions and mainnet-height inference', mismatch: selected.some(item => item.classification.mismatch), unclassified, duplicates};
    }
    function frozenEvidence(data, manifest, era) {
        if (!data || data.schema_version !== 1 || !Array.isArray(data.experiments)) return unknown('The frozen experiment snapshot is unavailable.');
        const only = containedEra(manifest, data.scope);
        if (!only) return unknown('The frozen studies lack an unambiguous single-era scope; results are not divided across eras.');
        if (only.id !== era.id) return {state: 'no_coverage', reason: 'The published experiments use a different historical era. No local experiment result is available here.'};
        const experiments = data.experiments.filter(item => item.status === 'completed' && item.evidence_kind === 'measured');
        return {state: experiments.length ? 'covered' : 'no_coverage', experiments, reason: 'No completed measured experiment is published for this era.', scope: data.scope};
    }
    function safeURL(value) {
        if (typeof value !== 'string' || /[\u0000-\u0020\u007f]/.test(value)) return null;
        if (/^https?:\/\//i.test(value)) {
            try {const url = new URL(value);return url.username || url.password ? null : url.href;} catch {return null;}
        }
        if (/^(?:index|todos|eras)\.html(?:[?#].*)?$/.test(value) || /^(?:protocol-eras|data|live-observations|research-progress)\.json$/.test(value)) return value;
        if (/^(?:brain|research)\/[a-zA-Z0-9_./-]+(?:#[a-zA-Z0-9_-]+)?$/.test(value) && !value.split(/[\/#]/).includes('..')) return `https://github.com/netzo92/deanonymizing_xmr/blob/main/${value}`;
        return null;
    }
    function readSelection(search, manifest) {
        const id = new URLSearchParams(search).get('era');
        return list(manifest?.eras).some(era => era.id === id) ? id : manifest?.eras?.[0]?.id || null;
    }
    function validateSource(key, value) {
        if (key === 'manifest') return validateManifest(value);
        if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('Invalid snapshot');
        if (key === 'historical' && (!value.summary || typeof value.summary !== 'object')) throw Error('Missing historical summary');
        if (key === 'live' && (value.schema_version !== 1 || !Array.isArray(value.blocks))) throw Error('Unsupported observer export');
        if (key === 'frozen' && (value.schema_version !== 1 || !Array.isArray(value.experiments))) throw Error('Unsupported experiment export');
        return value;
    }
    function applyResults(previous, results) {
        const next = {...previous};
        Object.keys(URLS).forEach((key, i) => {
            const result = results[i];
            next[key] = result.status === 'fulfilled' ? {data: result.value, error: null} : {data: previous[key]?.data || null, error: result.reason?.message || String(result.reason)};
        });
        return next;
    }
    function el(tag, className, text) {const node = document.createElement(tag);if (className) node.className = className;if (text !== undefined) node.textContent = String(text);return node;}
    function add(parent, ...children) {parent.append(...children.filter(Boolean));return parent;}
    function link(label, href) {const url = safeURL(href);if (!url) return el('span', '', label);const node = el('a', '', label);node.href = url;if (/^https?:/.test(url)) {node.target = '_blank';node.rel = 'noopener noreferrer';}return node;}
    function items(values) {const ul = el('ul');list(values).forEach(value => ul.append(el('li', '', value)));return ul;}
    function metricList(values) {const dl = el('dl', 'era-metrics');values.forEach(([label, value]) => dl.append(el('dt', '', label), el('dd', '', value)));return dl;}
    function date(value) {const parsed = Date.parse(value);return Number.isFinite(parsed) ? new Date(parsed).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC') : 'Unknown';}
    function range(era) {return `${fmt(era.start_height)}–${era.end_height === null ? 'present' : fmt(era.end_height)}`;}
    async function fallbackFetch(url) {
        const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 20000);
        try {const response = await fetch(url, {cache: 'no-store', signal: controller.signal});if (!response.ok) throw Error(`HTTP ${response.status}`);return await response.json();} finally {clearTimeout(timeout);}
    }
    function mount(host, options = {}) {
        const urls = {...URLS, ...options.urls};
        let sources = {}, selected = null, stopped = false, busy = false, timer = null, previous = null;
        const status = el('p', '', 'Checking four independently scoped sources…');status.setAttribute('role', 'status');
        const refresh = el('button', '', 'Refresh now');refresh.type = 'button';refresh.dataset.focus = 'refresh';
        const sourceStatus = el('div', 'era-source-status');
        const content = el('div');
        host.replaceChildren(add(el('div', 'era-refresh'), status, refresh), sourceStatus, content);
        function updateStatus() {
            sourceStatus.replaceChildren();
            Object.keys(URLS).forEach(key => {
                const source = sources[key], data = source?.data;
                let message = !source ? 'loading' : source.error ? data ? 'last available snapshot · check failed' : 'unavailable' : key === 'frozen' ? 'frozen snapshot' : key === 'manifest' ? 'pinned reference' : 'loaded';
                let stale = false;
                const observerError = key === 'live' && data && (data.state === 'error' || data.error);
                if (observerError && !source.error) message = 'observer reports an error';
                if (data && !source.error && !observerError && (key === 'historical' || key === 'live')) {
                    const stamp = key === 'live' ? data.last_success_at : data.scope?.exported_at;
                    const seconds = (Date.now() - Date.parse(stamp)) / 1000;
                    stale = Number.isFinite(seconds) && seconds > 600;
                    if (stale) message = 'snapshot older than 10 minutes';
                    else if (!Number.isFinite(seconds)) message = 'snapshot time unknown';
                    else if (seconds < -60) {stale = true;message = 'snapshot clock mismatch';}
                }
                const badge = el('span', `era-source-state${source?.error || stale || observerError ? ' is-warning' : ''}`, `${LABELS[key]} · ${message}`);
                badge.title = source?.error || (key === 'live' ? date(data?.last_success_at) : date(data?.scope?.exported_at || data?.generated_at));
                sourceStatus.append(badge);
            });
        }
        function evidenceCard(kind, title, evidence, data) {
            const card = el('article', `era-evidence-card ${kind}${evidence.state !== 'covered' ? ' is-empty' : ''}`);
            card.append(el('span', 'era-card-kind', title));
            if (evidence.state !== 'covered') {
                card.append(el('h4', '', evidence.state === 'unknown' ? 'Evidence unavailable' : 'No coverage in this era'), el('p', '', evidence.reason), el('span', 'era-state', evidence.state === 'unknown' ? 'Unknown · not zero' : 'Not measured here'));
                return card;
            }
            if (kind === 'historical') {
                const metrics = evidence.metrics;
                card.append(el('h4', '', 'What the analyzer currently labels'), el('p', 'era-card-value', fmt(metrics.total_rings)), el('span', 'era-card-unit', 'input rings in the historical scan'));
                card.append(metricList([['Originally one-member rings', fmt(metrics.original_singleton_rings)], ['Originally multi-member rings', fmt(metrics.original_multimember_rings)], ['Deterministic one-member claims', fmt(metrics.deterministic_singleton_resolutions)], ['Deterministic multi-member claims', fmt(metrics.deterministic_multimember_resolutions)], ['ML-derived hypotheses', fmt(metrics.hypothesis_resolutions)], ['Reduced, still unresolved', fmt(metrics.unresolved_reduced)], ['Unchanged, unresolved', fmt(metrics.unresolved_unchanged)], ['Conflicting rings', fmt(metrics.conflict_rings)]]));
                card.append(el('p', '', 'Stored deterministic claims depend on the analyzer and its inputs. One-member rings already expose one candidate; they are shown separately from multi-member analysis.'), el('span', 'era-state', evidence.basis));
                if (evidence.assumption) card.append(el('p', 'era-warning', evidence.assumption));
                card.append(link('Inspect historical evidence →', 'index.html#predictions'));
            } else if (kind === 'live') {
                card.append(el('h4', '', 'Recent activity, without resolution labels'), el('p', 'era-card-value', fmt(evidence.countedInputs)), el('span', 'era-card-unit', 'input rings counted in retained block samples'));
                card.append(metricList([['Retained sampled blocks', fmt(evidence.blocks)], ['Non-miner transactions', fmt(evidence.transactions)], ['Blocks with complete ring counts', `${fmt(evidence.completeBlocks)} / ${fmt(evidence.blocks)}`], ['Observed ring sizes', evidence.ringSizes.length ? evidence.ringSizes.join(', ') : 'Unknown']]));
                card.append(el('p', '', `Sample heights ${fmt(evidence.firstHeight)}–${fmt(evidence.lastHeight)}. These counts describe activity. The observer supplies no resolved-spend, real-member, or wallet-ownership labels.`), el('span', 'era-state', evidence.basis));
                if (evidence.completeBlocks < evidence.blocks) card.append(el('p', 'era-warning', 'Input totals include only fully decoded blocks. Missing details stay unknown.'));
                if (evidence.mismatch) card.append(el('p', 'era-warning', 'Some recorded versions disagree with the mainnet-height mapping. These blocks follow their recorded version; verify the source and network.'));
                if (data?.state === 'error' || data?.error) card.append(el('p', 'era-warning', 'The observer reports an error. Previously retained observations remain visible.'));
                if (list(data?.gaps).length) card.append(el('p', 'era-warning', `${data.gaps.length} recorded observation gaps; the retained sample is not a complete chain archive.`));
                card.append(link('Explore recent block observations →', 'index.html#live-observations'));
            } else {
                card.append(el('h4', '', 'Completed studies on a frozen sample'), el('p', 'era-card-value', fmt(evidence.experiments.length)), el('span', 'era-card-unit', 'completed experiments, with their own sample sizes'));
                evidence.experiments.forEach(experiment => {
                    const study = add(el('section', 'era-study'), el('h5', '', experiment.title), el('p', '', experiment.finding));
                    if (experiment.metric) study.append(el('p', '', `${fmt(experiment.metric.value)} ${experiment.metric.label || ''}`));
                    study.append(link('Result and limitations →', `todos.html#results`));card.append(study);
                });
                card.append(el('p', 'era-warning', 'Baseline figures are selective retrospective agreement with stored labels, not prediction accuracy. No measured gain across security upgrades is established.'));
            }
            return card;
        }
        function renderDetail(era, target, manifest, historical, live, frozen) {
            const head = add(el('div', 'era-detail-top'), add(el('div'), el('p', 'era-kicker', era.period_label || 'Protocol era'), el('h2', '', era.label), el('p', '', `Mainnet heights ${range(era)} · inclusive boundaries`)), el('span', 'era-version', `HF ${era.versions.join(' / ')}`));
            const rules = add(el('div', 'era-rule-grid'), add(el('section'), el('h3', '', 'What changed in the protocol'), items(era.changes)), add(el('section'), el('h3', '', 'What that implies in theory'), items(era.theoretical_implications)));
            const title = add(el('div', 'era-evidence-title'), el('h3', '', 'What evidence do we have for this era?'), el('p', '', 'Three evidence sources. Each count keeps its own population and date; historical research samples can overlap.'));
            const grid = add(el('div', 'era-evidence-grid'), evidenceCard('historical', 'Historical analysis', historical, sources.historical?.data), evidenceCard('live', 'Recent block observations', live, sources.live?.data), evidenceCard('frozen', 'Frozen experiment findings', frozen, sources.frozen?.data));
            const conclusion = add(el('section', 'era-conclusion'), el('h3', '', 'What we can conclude today'), el('p', '', historical.state === 'covered' ? 'We can describe this sampled era and inspect the analyzer’s stored claims. We cannot attribute a difference in success to a protocol upgrade without comparable, independently evaluated cohorts on both sides.' : live.state === 'covered' ? 'We can describe recent activity and the ring sizes in these sampled blocks. There are no local resolution labels here from which to measure heuristic success or failure.' : 'The source documents a protocol change. Our available datasets do not establish how the research performs in this era. No coverage is not evidence of zero success.'), items(era.claim_limits));
            const next = el('div', 'era-next');next.append(el('span', '', 'Next questions to test'));
            list(era.suggested_experiments).forEach(item => next.append(link(`${item.id} · ${item.label}`, item.href)));
            if (!list(era.suggested_experiments).some(item => item.id === 'ER3')) next.append(link('ER3 · Compare matched upgrade cohorts', 'todos.html?status=all&task=ER3#tasks'));
            if (!list(era.suggested_experiments).length) next.append(link('Open research checklist →', 'todos.html?status=open#tasks'));
            const provenance = el('details', 'era-provenance');provenance.dataset.disclosure = 'era-provenance';
            provenance.append(el('summary', '', 'Sources, fork transitions & evidence provenance'));
            provenance.append(el('p', '', manifest.height_semantics || 'Era boundaries use mainnet activation heights.'));
            provenance.append(add(el('p'), link('Research plan and interpretation →', 'brain/research/security-eras.md')));
            provenance.append(el('p', '', `Pinned Monero ${manifest.source_pin?.tag || 'release unknown'} · ${manifest.source_pin?.commit || 'revision unknown'}. Height-based assignments are inferred unless the observer records a block major version.`));
            const links = el('ul');list(era.sources).forEach(source => links.append(add(el('li'), link(source.label || 'Protocol source', source.url))));provenance.append(links);
            list(manifest.forks).filter(fork => fork.era_id === era.id).forEach(fork => {
                provenance.append(el('p', '', `HF ${fork.version} · starts at height ${fmt(fork.start_height)}`), items(fork.transition_notes));
                list(fork.sources).forEach(source => provenance.append(add(el('p'), link(source.label || 'Fork source', source.url))));
            });
            const h = sources.historical?.data, l = sources.live?.data, f = sources.frozen?.data;
            provenance.append(el('p', '', `Historical dataset: ${h?.scope?.dataset_id || 'unknown'} · scan ${fmt(h?.scope?.scan_start)}–${fmt(h?.scope?.scan_end)} · export ${date(h?.scope?.exported_at)}. Era method: ${historical.basis || historical.reason}`));
            provenance.append(el('p', '', h?.protocol_eras?.network_basis || 'Declared mainnet height mapping. This historical export does not independently establish network identity or recorded block versions.'));
            provenance.append(el('p', '', `Historical block versions: ${h?.protocol_eras?.observed_block_versions === true ? 'reported as recorded by the exporter' : 'not recorded or not established'}. Mapping SHA-256: ${h?.protocol_eras?.mapping_sha256 || 'not supplied by this export'}.`));
            provenance.append(el('p', '', `Recent observer: ${l?.source?.origin || 'unknown'} · last success ${date(l?.last_success_at)} · revision ${l?.source?.source_revision || 'unknown'}. Block fetch time is not transaction first-seen time. Unclassified records: ${fmt(live.unclassified)}; excluded duplicate-height records: ${fmt(live.duplicates)}.`));
            provenance.append(el('p', '', `Frozen research: scan ${fmt(f?.scope?.scan_start)}–${fmt(f?.scope?.scan_end)} · published ${date(f?.generated_at)} · source database SHA-256 ${f?.scope?.main_database_sha256 || 'unknown'}. Completed studies are not refreshed by incoming blocks.`));
            list(frozen.experiments).forEach(experiment => provenance.append(add(el('p'), link(experiment.title || experiment.id, experiment.artifact_path), ` · revision ${experiment.source_revision || 'unknown'} · SHA-256 ${experiment.artifact_sha256 || 'unknown'}. ${experiment.limitation || ''}`)));
            target.replaceChildren(head, rules, title, grid, conclusion, next, provenance);
        }
        function render() {
            const manifest = sources.manifest?.data;
            const focusKey = host.contains(document.activeElement) ? document.activeElement?.dataset.focus : null;
            const open = new Set([...content.querySelectorAll('details[open]')].map(node => node.dataset.disclosure));
            const timelineScroll = content.querySelector('.era-timeline')?.scrollLeft || 0;
            if (!manifest) {
                content.replaceChildren(add(el('div', 'era-loading'), el('h2', '', 'The era reference is unavailable'), el('p', '', 'The timeline needs a valid mainnet protocol manifest. Use Refresh now to retry; other last available snapshots are retained.')));return;
            }
            if (!manifest.eras.some(era => era.id === selected)) selected = readSelection(root.location?.search || '', manifest);
            const entries = manifest.eras.map(era => ({era, historical: historicalEvidence(sources.historical?.data, manifest, era), live: liveEvidence(sources.live?.data, manifest, era), frozen: frozenEvidence(sources.frozen?.data, manifest, era)}));
            const overview = el('div', 'era-overview');
            const coverageTotal = key => entries.every(entry => entry[key].state === 'unknown') ? 'Unknown' : `${entries.filter(entry => entry[key].state === 'covered').length}${entries.some(entry => entry[key].state === 'unknown') ? '+' : ''}`;
            [[manifest.eras.length, 'protocol eras in the reference'], [coverageTotal('historical'), 'eras with historical analysis'], [coverageTotal('live'), 'eras with recent block samples']].forEach(([n,label]) => overview.append(add(el('div'), el('strong', '', n), el('span', '', label))));
            const selection = el('section');selection.id = 'era-selection';
            const select = el('select');select.id = 'era-select';select.dataset.focus = 'era-select';
            manifest.eras.forEach(era => {const option = el('option', '', era.label);option.value = era.id;select.append(option);});select.value = selected;
            const selectLabel = add(el('label', 'era-select-label', 'Explore an era'), select);
            selection.append(add(el('div', 'era-selection-head'), add(el('div'), el('h2', '', 'Follow the protocol milestones'), el('p', '', 'Select a milestone. Equal spacing shows sequence, not elapsed time.')), selectLabel));
            const timeline = el('div', 'era-timeline');timeline.setAttribute('role', 'group');timeline.setAttribute('aria-label', 'Protocol era milestones; use arrow keys to explore');
            entries.forEach(({era,historical,live}) => {
                const button = el('button', 'era-stop');button.type = 'button';button.dataset.era = era.id;button.dataset.focus = `timeline-${era.id}`;button.setAttribute('aria-pressed', String(era.id === selected));button.setAttribute('aria-controls', 'era-detail');
                button.append(el('small', '', `HF ${era.versions.join(' / ')} · ${era.period_label || ''}`), el('strong', '', era.label), el('span', 'era-stop-coverage', historical.state === 'covered' ? 'Historical analysis' : live.state === 'covered' ? 'Recent observations' : historical.state === 'unknown' || live.state === 'unknown' ? 'Coverage unknown' : 'Coverage gap'));
                button.addEventListener('click', () => choose(era.id, `timeline-${era.id}`));
                timeline.append(button);
            });
            timeline.addEventListener('keydown', event => {
                const index = manifest.eras.findIndex(era => era.id === event.target.dataset.era);if (index < 0) return;
                const next = event.key === 'ArrowRight' ? Math.min(manifest.eras.length - 1, index + 1) : event.key === 'ArrowLeft' ? Math.max(0, index - 1) : event.key === 'Home' ? 0 : event.key === 'End' ? manifest.eras.length - 1 : null;
                if (next === null) return;event.preventDefault();const id = manifest.eras[next].id;choose(id, `timeline-${id}`);
            });
            select.addEventListener('change', () => choose(select.value, 'era-select'));
            selection.append(timeline);
            const detail = el('section', 'era-detail');detail.id = 'era-detail';
            const chosen = entries.find(entry => entry.era.id === selected);
            renderDetail(chosen.era, detail, manifest, chosen.historical, chosen.live, chosen.frozen);
            const comparison = add(el('section', 'era-comparison'), el('h2', '', 'Compare coverage across eras'), el('p', '', 'This is a map of available evidence, not a ranking of privacy or attack success. Missing observations and missing research results are shown explicitly.'));comparison.id = 'era-comparison';
            const wrap = el('div', 'era-matrix'), table = el('table'), head = el('thead'), header = el('tr');
            const columns = ['Protocol era', 'Historical input rings', 'Deterministic multi-member claims', 'Recent sampled blocks', 'Completed frozen studies'];
            columns.forEach(label => {const th = el('th', '', label);th.scope = 'col';header.append(th);});head.append(header);table.append(head);
            const body = el('tbody');
            entries.forEach(({era,historical,live,frozen}) => {
                const row = el('tr', era.id === selected ? 'is-selected' : '');
                const button = el('button', '', era.label);button.type = 'button';button.dataset.focus = `matrix-${era.id}`;button.setAttribute('aria-pressed', String(era.id === selected));button.addEventListener('click', () => {choose(era.id, `matrix-${era.id}`);document.getElementById('era-detail')?.scrollIntoView({block: 'start'});});
                row.append(add(el('td'), add(el('span'), button, el('small', '', `HF ${era.versions.join('/')} · ${range(era)}`))));
                [[historical, historical.metrics?.total_rings], [historical, historical.metrics?.deterministic_multimember_resolutions], [live, live.blocks], [frozen, frozen.experiments?.length]].forEach(([evidence,value], index) => {
                    const td = el('td');td.dataset.label = columns[index + 1];
                    td.append(el('span', evidence.state !== 'covered' ? 'no-coverage' : '', evidence.state === 'covered' ? fmt(value) : evidence.state === 'unknown' ? 'Unknown' : 'No coverage'));row.append(td);
                });body.append(row);
            });table.append(body);wrap.append(table);comparison.append(wrap);
            const aggregate = sources.historical?.data?.protocol_eras;
            if (count(aggregate?.coverage?.unknown_rings) && aggregate.coverage.unknown_rings > 0) comparison.append(el('p', 'era-matrix-note', `${fmt(aggregate.coverage.unknown_rings)} historical rings are unclassified and excluded from the era rows; they remain in the overall dataset denominator.`));
            comparison.append(el('p', 'era-matrix-note', 'Rings are grouped by their transaction era. Labels describe current stored claims, not what was knowable when that era was active. Deterministic multi-member claims exclude originally one-member rings and ML-derived hypotheses; recent blocks supply activity counts only.'));
            const mirror = root.location?.hostname?.endsWith('.github.io') ? add(el('p', 'notice info'), 'GitHub Pages serves published snapshots. For incoming collector exports, open ', link('the live dashboard', 'http://35.254.148.94/eras.html'), '.') : null;
            content.replaceChildren(...[mirror, overview, selection, detail, comparison].filter(Boolean));
            content.querySelectorAll('details').forEach(node => {node.open = open.has(node.dataset.disclosure);});
            const occurrences = new Map();
            content.querySelectorAll('a, summary').forEach(node => {
                if (node.dataset.focus) return;
                const key = node.tagName === 'SUMMARY' ? `disclosure-${node.parentElement.dataset.disclosure}` : `link-${node.getAttribute('href')}-${node.textContent}`;
                const occurrence = occurrences.get(key) || 0;occurrences.set(key, occurrence + 1);node.dataset.focus = `${key}-${occurrence}`;
            });
            timeline.scrollLeft = timelineScroll;
            if (focusKey) [...host.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === focusKey)?.focus({preventScroll: true});
        }
        function choose(id, focusKey) {
            selected = id;
            const url = new URL(root.location.href);url.searchParams.set('era', id);root.history.replaceState({}, '', url);
            render();
            if (focusKey) {
                const focus = [...content.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === focusKey);
                focus?.focus({preventScroll: true});
                if (focus?.classList.contains('era-stop')) focus.scrollIntoView({block: 'nearest', inline: 'nearest'});
            }
        }
        async function check() {
            if (busy || stopped || document.hidden && !options.ignoreVisibility) return false;
            busy = true;refresh.disabled = true;host.setAttribute('aria-busy', 'true');
            try {
                const fetcher = options.fetchJSON || root.TraceGroveLive?.fetchJSON || fallbackFetch;
                const results = await Promise.allSettled(Object.keys(URLS).map(key => Promise.resolve().then(() => fetcher(urls[key])).then(data => validateSource(key, data))));
                if (stopped) return false;
                sources = applyResults(sources, results);
                const key = JSON.stringify(sources), changed = key !== previous;
                if (changed) {render();previous = key;}
                updateStatus();
                const failures = Object.values(sources).filter(source => source.error).length;
                status.textContent = `${failures ? `${failures} source check${failures === 1 ? '' : 's'} failed; keeping any last available snapshots.` : 'Source checks complete.'} Checked ${date(new Date().toISOString())}. Page checks every 60 seconds; frozen studies stay frozen.`;
                return changed;
            } finally {busy = false;refresh.disabled = false;host.setAttribute('aria-busy', 'false');}
        }
        refresh.addEventListener('click', check);
        const resume = () => {if (!document.hidden) check();};
        const navigate = () => {if (sources.manifest?.data) {selected = readSelection(root.location.search, sources.manifest.data);render();}};
        root.addEventListener('popstate', navigate);
        if (options.polling !== false) {timer = setInterval(check, 60000);document.addEventListener('visibilitychange', resume);}
        const ready = check();
        return {ready, refresh: check, getState: () => ({selected, sources}), stop() {stopped = true;clearInterval(timer);document.removeEventListener('visibilitychange', resume);root.removeEventListener('popstate', navigate);}};
    }
    return {validateManifest, eraAtHeight, containedEra, validRowCounts, historicalEvidence, classifyBlock, liveEvidence, frozenEvidence, safeURL, readSelection, validateSource, applyResults, mount};
});
