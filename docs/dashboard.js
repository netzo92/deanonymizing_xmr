'use strict';

// Keep identifiers as strings: legacy amount buckets may exceed JS integer precision.
const COLORS = { green: '#69cf89', purple: '#c8a4ff', yellow: '#e7c467', blue: '#79b8ff', gray: '#758394', orange: '#ff8b4c' };
const pendingCharts = [];
const isNumber = value => typeof value === 'number' && Number.isFinite(value);
const known = value => value !== null && value !== undefined && value !== '';
const display = value => known(value) ? String(value) : 'Unknown';
const count = value => isNumber(value) ? value.toLocaleString() : 'Unknown';
const score = value => isNumber(value) ? value.toFixed(4) : 'Unknown';
const list = value => Array.isArray(value) ? value : [];
const short = value => known(value) ? String(value).length > 20 ? `${String(value).slice(0, 10)}…${String(value).slice(-6)}` : String(value) : 'Unknown';
const identity = output => output && known(output.amount) && known(output.index) ? `(${output.amount}, ${output.index})` : 'Unknown';
const selectedOutput = row => ({ amount: row.predicted_amount, index: row.predicted_output_index });
const sameOutput = (a, b) => a && b && known(a.amount) && known(a.index) && String(a.amount) === String(b.amount) && String(a.index) === String(b.index);
const percentage = (part, whole) => isNumber(part) && isNumber(whole) && whole > 0 ? `${(100 * part / whole).toFixed(1)}%` : 'N/A';
const deterministic = resolution => resolution?.confidence === 1 && isNumber(resolution.pass_num) && resolution.pass_num >= 0;

function outcome(row) {
    if (row.verified === false || row.verified === 0) return 'pending';
    if (row.verified === true || row.verified === 1) {
        if (row.correct === true || row.correct === 1) return 'correct';
        if (row.correct === false || row.correct === 0) return 'wrong';
    }
    return 'unknown';
}

function acceptance(row) {
    if (row.accepted === true || row.accepted === 1) return 'accepted';
    if (row.accepted === false || row.accepted === 0) return 'below';
    return 'unknown';
}

function evidenceCategories(summary) {
    const entries = [
        ['Deterministic resolutions', summary.deterministic_resolutions, 'green'],
        ['Hypothesis resolutions', summary.hypothesis_resolutions, 'purple'],
        ['Unresolved · reduced', summary.unresolved_reduced, 'yellow'],
        ['Unresolved · unchanged', summary.unresolved_unchanged, 'blue'],
    ];
    const complete = entries.every(entry => isNumber(entry[1]) && entry[1] >= 0);
    const sum = complete ? entries.reduce((total, entry) => total + entry[1], 0) : null;
    return { entries, complete, reconciles: complete && isNumber(summary.total_rings) && sum === summary.total_rings, sum };
}

function historyCohorts(history, datasetId) {
    const cohorts = { current: [], unknown: [], other: [] };
    for (const snapshot of list(history)) {
        if (!known(snapshot.dataset_id)) cohorts.unknown.push(snapshot);
        else if (known(datasetId) && String(snapshot.dataset_id) === String(datasetId)) cohorts.current.push(snapshot);
        else cohorts.other.push(snapshot);
    }
    return cohorts;
}

function filterRows(rows, filters = {}) {
    const query = String(filters.query || '').trim().toLowerCase();
    const threshold = value => known(value) && Number.isFinite(Number(value)) ? Number(value) : null;
    const minScore = threshold(filters.minScore);
    const minHeight = threshold(filters.minHeight);
    const maxHeight = threshold(filters.maxHeight);
    const ringSize = threshold(filters.ringSize);
    return rows.filter(row => {
        const terms = [row.key_image, ...list(row.tx_hashes), row.run_id, row.prediction_id, row.predicted_amount, row.predicted_output_index, identity(selectedOutput(row))];
        if (query && !terms.some(value => String(value ?? '').toLowerCase().includes(query))) return false;
        if (minScore !== null && (!isNumber(row.confidence) || row.confidence < minScore)) return false;
        if (minHeight !== null && (!isNumber(row.block_height) || row.block_height < minHeight)) return false;
        if (maxHeight !== null && (!isNumber(row.block_height) || row.block_height > maxHeight)) return false;
        if (ringSize !== null && row.original_ring_size !== ringSize) return false;
        if (filters.outcome && filters.outcome !== 'all' && outcome(row) !== filters.outcome) return false;
        if (filters.acceptance && filters.acceptance !== 'all' && acceptance(row) !== filters.acceptance) return false;
        return true;
    });
}

function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
}

function append(parent, ...children) {
    for (const child of children) if (child !== null && child !== undefined) parent.append(child);
    return parent;
}

function notice(parent, text, kind = '') {
    return append(parent, element('p', `notice ${kind}`, text));
}

function heading(parent, title, text) {
    const wrapper = element('div', 'section-heading');
    const titles = append(element('div'), element('h2', '', title));
    if (text) titles.append(element('p', '', text));
    append(parent, append(wrapper, titles));
    return wrapper;
}

function table(parent, headers, rows, caption) {
    const wrap = element('div', 'table-scroll');
    wrap.tabIndex = 0;
    wrap.setAttribute('role', 'region');
    wrap.setAttribute('aria-label', caption || headers.join(', '));
    const result = element('table');
    if (caption) result.append(element('caption', 'sr-only', caption));
    const header = element('tr');
    for (const text of headers) { const cell = element('th', '', text); cell.scope = 'col'; header.append(cell); }
    result.append(append(element('thead'), header));
    const body = element('tbody');
    for (const row of rows) {
        const tr = element('tr');
        for (const value of row) tr.append(append(element('td'), value instanceof Node ? value : document.createTextNode(display(value))));
        body.append(tr);
    }
    append(parent, append(wrap, append(result, body)));
    return body;
}

function details(parent, title, data) {
    const item = append(element('details'), element('summary', '', title));
    item.append(element('pre', '', JSON.stringify(data, null, 2)));
    parent.append(item);
}

function facts(parent, pairs) {
    const dl = element('dl', 'facts');
    for (const [name, value] of pairs) append(dl, element('dt', '', name), element('dd', '', display(value)));
    parent.append(dl);
}

function statusBadge(value) {
    return element('span', `badge ${value === 'unknown' ? 'neutral' : value}`, value[0].toUpperCase() + value.slice(1));
}

function renderSummary(app, data) {
    const s = data.summary || {};
    const scope = data.scope || {};
    const bar = element('div', 'scope');
    for (const [label, value] of [
        ['Dataset', scope.dataset_id], ['Scanned heights', `${display(scope.scan_start)} – ${display(scope.scan_end)}`],
        ['Exported', scope.exported_at || data.generated_at], ['Schema', data.schema_version || 'Legacy'],
    ]) bar.append(append(element('span'), `${label}: `, element('strong', label === 'Dataset' ? 'mono' : '', display(value))));
    if (known(scope.chain_data_at)) bar.append(append(element('span'), 'Last scanned block: ', element('strong', '', display(scope.chain_data_at))));
    if (isNumber(data.collection?.blocks_behind)) bar.append(append(element('span'), 'Collector lag: ', element('strong', '', `${count(data.collection.blocks_behind)} blocks behind the observed node`)));
    app.append(bar);
    const categories = evidenceCategories(s);
    if (!categories.complete) notice(app, 'This export does not contain the complete evidence split. Deterministic and hypothesis counts are unknown where missing; legacy “fully resolved” totals can include hypotheses. Regenerate the dashboard export for full detail.');
    else if (!categories.reconciles) notice(app, `The four evidence categories sum to ${count(categories.sum)}, while total rings is ${count(s.total_rings)}. This partial or inconsistent export should not be interpreted as a reconciled breakdown.`, 'danger');
    if (isNumber(data.schema_version) && data.schema_version > 2) notice(app, 'This export uses a newer schema. Only recognized fields are displayed; consult the raw export for additional information.', 'info');
    const grid = element('div', 'stats-grid');
    const items = [
        ['Total rings', s.total_rings, '', `${count(s.blocks_scanned)} blocks scanned`],
        ...categories.entries.map(([label, value, color]) => [label, value, color, `${percentage(value, s.total_rings)} of all rings`]),
        ['Conflict flags', s.conflict_rings, 'red', 'Overlapping diagnostic count'],
    ];
    for (const [label, value, color, hint] of items) {
        grid.append(append(element('div', 'stat-card'), element('div', 'label', label), element('div', `value ${color}`, count(value)), element('div', 'detail', hint)));
    }
    app.append(grid);
    const legacy = !categories.complete && isNumber(s.fully_resolved) ? ` Legacy stored resolutions: ${count(s.fully_resolved)} of ${count(s.total_rings)} rings (${percentage(s.fully_resolved, s.total_rings)}); evidence kind unknown.` : '';
    app.append(element('p', 'summary-note', `Deterministic is the stored evidence classification, conditional on analysis assumptions. Reduced counts reflect the current analysis and can include hypothesis-derived eliminations. Conflicts overlap categories.${legacy}`));
    if (s.orphan_resolution_claims > 0) notice(app, `${count(s.orphan_resolution_claims)} stored resolution claims have no ring membership and are excluded from the ring category totals.`);
}

function renderBrowser(app, data) {
    const browser = data.prediction_browser;
    const section = element('section', 'card');
    section.id = 'predictions';
    heading(section, 'Prediction browser', 'Historical scoring records, including accepted predictions and scores below the run threshold.');
    app.append(section);
    if (!browser || !Array.isArray(browser.rows)) {
        const empty = element('div', 'empty-state');
        append(empty, element('p', '', 'Prediction records are not included in this export.'), element('p', '', 'Regenerate with python main.py export-viz to include a bounded prediction browser.'));
        section.append(empty);
        return;
    }
    const rows = browser.rows.filter(row => row && typeof row === 'object');
    const total = isNumber(browser.total) ? browser.total : null;
    const ordering = browser.order === 'newest_first' ? 'newest first' : 'export order';
    section.append(element('p', 'hint', `${count(rows.length)} records included of ${count(total)} historical scoring records; ${ordering}; export limit ${count(browser.limit)}. Filters and pagination apply only to the included records. Multiple runs can contain the same ring.`));
    if (isNumber(browser.exported) && browser.exported !== rows.length) notice(section, `The export declares ${count(browser.exported)} records but contains ${count(rows.length)} readable rows. This view uses the rows actually available.`, 'danger');
    if (total !== null && total > rows.length) notice(section, `Bounded export: ${count(total - rows.length)} historical records are outside this view. Matching rows in the full database may be omitted.`, 'info');
    const form = element('form', 'filters');
    form.setAttribute('aria-label', 'Filter exported prediction records');
    // This template contains no export data. All data-dependent values use textContent.
    form.innerHTML = `
        <label class="search-filter"><span>Search identifiers</span><input name="query" type="search" placeholder="Key image, transaction, output, run…"></label>
        <label><span>Minimum model score (0–1)</span><input name="minScore" type="number" min="0" max="1" step="0.01" placeholder="Any"></label>
        <label><span>Verification outcome</span><select name="outcome"><option value="all">All outcomes</option><option value="pending">Pending</option><option value="correct">Correct</option><option value="wrong">Wrong</option><option value="unknown">Unknown</option></select></label>
        <label><span>Threshold decision</span><select name="acceptance"><option value="all">All decisions</option><option value="accepted">Accepted</option><option value="below">Below threshold</option><option value="unknown">Unknown</option></select></label>
        <label><span>Original ring size</span><input name="ringSize" type="number" min="1" step="1" placeholder="Any"></label>
        <label><span>Transaction height from</span><input name="minHeight" type="number" min="0" step="1" placeholder="Any"></label>
        <label><span>Transaction height to</span><input name="maxHeight" type="number" min="0" step="1" placeholder="Any"></label>
        <div class="filter-actions"><button type="reset">Reset filters</button></div>`;
    section.append(form);
    section.append(element('p', 'hint', 'Model scores are uncalibrated ranking scores, not established probabilities. Active numeric filters exclude rows with unknown values. Verification and threshold acceptance are separate fields.'));
    const tableHost = element('div');
    section.append(tableHost);
    const pagination = element('div', 'pagination');
    const pageStatus = element('p', 'hint');
    pageStatus.setAttribute('role', 'status');
    const controls = element('div', 'page-buttons');
    const pageLabel = element('label', '', 'Rows per page ');
    const pageSizeControl = element('select');
    pageSizeControl.setAttribute('aria-label', 'Rows per page');
    for (const size of [10, 25, 50]) { const option = element('option', '', size); option.value = size; pageSizeControl.append(option); }
    pageLabel.append(pageSizeControl);
    const previous = element('button', '', 'Previous');
    const next = element('button', '', 'Next');
    append(section, append(pagination, pageStatus, append(controls, pageLabel, previous, next)));
    const inspector = element('section', 'card inspector');
    inspector.id = 'evidence-inspector';
    inspector.tabIndex = -1;
    inspector.setAttribute('aria-label', 'Selected prediction evidence');
    append(inspector, element('h2', '', 'Evidence inspector'), element('p', 'hint', 'Select a prediction to inspect its frozen scores and current supporting evidence.'));
    app.append(inspector);
    let page = 0;
    let selected = null;
    const filters = () => Object.fromEntries(new FormData(form).entries());
    function refresh() {
        const filtered = filterRows(rows, filters());
        const pageSize = Number(pageSizeControl.value);
        const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
        page = Math.min(page, pages - 1);
        const visible = filtered.slice(page * pageSize, (page + 1) * pageSize);
        tableHost.replaceChildren();
        const body = table(tableHost, ['Ring / transaction', 'Predicted (amount, index)', 'Model score', 'Run / scan height', 'Ring size / tx height', 'Decision', 'Outcome'], visible.map(row => {
            const keyCell = element('div');
            const button = element('button', 'row-link mono', short(row.key_image));
            button.type = 'button';
            button.title = display(row.key_image);
            button.setAttribute('aria-label', `Inspect prediction ${display(row.prediction_id)} for ring ${display(row.key_image)}`);
            button.setAttribute('aria-controls', inspector.id);
            button.setAttribute('aria-pressed', String(selected === row));
            button.addEventListener('click', () => {
                selected = row;
                renderInspector(inspector, row, list(browser.runs));
                refresh();
                inspector.focus({ preventScroll: true });
                inspector.scrollIntoView({ behavior: 'auto', block: 'start' });
            });
            append(keyCell, button, element('span', 'secondary mono', list(row.tx_hashes).length ? `${short(row.tx_hashes[0])}${row.tx_hashes.length > 1 ? ` +${row.tx_hashes.length - 1}` : ''}` : 'Transaction unknown'));
            const run = append(element('div'), element('span', 'mono', short(row.run_id)), element('span', 'secondary', `Scan: ${count(row.scan_height)}`));
            const size = append(element('div'), element('span', '', count(row.original_ring_size)), element('span', 'secondary', `Tx: ${count(row.block_height)}`));
            const decision = acceptance(row);
            return [keyCell, element('code', '', identity(selectedOutput(row))), element('span', 'nowrap', score(row.confidence)), run, size, element('span', `badge ${decision === 'accepted' ? 'purple' : 'neutral'}`, decision === 'accepted' ? 'Accepted' : decision === 'below' ? 'Below threshold' : 'Unknown'), statusBadge(outcome(row))];
        }), 'Historical predictions in the exported subset');
        [...body.children].forEach((tr, i) => { if (visible[i] === selected) tr.classList.add('selected'); });
        if (!visible.length) {
            const td = element('td', 'empty-state', rows.length ? 'No included records match these filters.' : 'No scoring records are included in this export.');
            td.colSpan = 7;
            body.append(append(element('tr'), td));
        }
        const first = filtered.length ? page * pageSize + 1 : 0;
        pageStatus.textContent = `${first}–${Math.min((page + 1) * pageSize, filtered.length)} of ${count(filtered.length)} matching included records · Page ${page + 1} of ${pages}`;
        previous.disabled = page === 0;
        next.disabled = page >= pages - 1;
    }
    form.addEventListener('submit', event => event.preventDefault());
    form.addEventListener('input', () => { page = 0; refresh(); });
    form.addEventListener('reset', () => { setTimeout(() => { page = 0; refresh(); }, 0); });
    pageSizeControl.addEventListener('change', () => { page = 0; refresh(); });
    previous.addEventListener('click', () => { page--; refresh(); });
    next.addEventListener('click', () => { page++; refresh(); });
    refresh();
    return row => {
        const match = rows.find(item => String(item.prediction_id) === String(row.prediction_id));
        if (!match) return;
        selected = match;
        renderInspector(inspector, match, list(browser.runs));
        refresh();
        inspector.focus({ preventScroll: true });
        inspector.scrollIntoView({ behavior: 'auto', block: 'start' });
    };
}

function renderInspector(parent, row, runs) {
    parent.replaceChildren();
    const title = heading(parent, 'Evidence inspector', `Historical prediction ${display(row.prediction_id)} · ${display(row.created_at)}`);
    const back = element('a', '', 'Back to predictions ↑');
    back.href = '#predictions';
    title.append(back);
    const evidence = row.evidence || {};
    const memory = evidence.memory || {};
    const prediction = selectedOutput(row);
    const grid = element('div', 'inspector-grid');
    const frozen = append(element('div'), element('h3', '', 'Selected historical prediction'));
    facts(frozen, [
        ['Key image', row.key_image], ['Transactions', list(row.tx_hashes).join(', ') || null],
        ['Predicted output', identity(prediction)], ['Model score', `${score(row.confidence)} · uncalibrated`],
        ['Threshold decision', acceptance(row) === 'below' ? 'Below threshold' : acceptance(row)],
        ['Outcome', outcome(row)], ['Verification timing', row.verification_timing],
        ['Verified at height', row.verification_height], ['Verification origin', row.verification_provenance],
        ['Prediction scan', row.scan_height], ['Run', row.run_id],
    ]);
    const current = append(element('div'), element('h3', '', 'Current exported evidence'));
    const resolution = memory.resolution;
    facts(current, [
        ['Resolution', resolution ? `${deterministic(resolution) ? 'Deterministic' : 'Hypothesis'} · ${identity(resolution.output)}` : Object.hasOwn(memory, 'resolution') && resolution === null ? 'Unresolved' : 'Unknown'],
        ['Original candidates', evidence.candidate_count ?? (Array.isArray(memory.members) ? memory.members.length : null)],
        ['Remaining', evidence.remaining_candidate_count ?? (Array.isArray(evidence.remaining_candidates) ? evidence.remaining_candidates.length : null)],
        ['Latest stored guess', memory.prediction ? `${identity(memory.prediction.output)} · score ${score(memory.prediction.confidence)}` : 'None / unknown'],
        ['Conflicts', Array.isArray(evidence.conflicts) ? evidence.conflicts.length : null],
    ]);
    append(parent, append(grid, frozen, current));
    parent.append(element('p', 'hint', 'The selected prediction and candidate scores were recorded at scoring time. Eliminations, remaining candidates, related rings, and the resolution trace describe the current export snapshot; they do not reconstruct the evidence available when the prediction was made.'));
    if (memory.prediction && (!sameOutput(prediction, memory.prediction.output) || row.confidence !== memory.prediction.confidence)) notice(parent, 'The latest stored guess differs from this historical prediction. The selected record above is preserved; the current guess does not replace it.', 'info');
    const run = runs.find(item => String(item.run_id) === String(row.run_id));
    if (run) {
        details(parent, 'Prediction run metadata and model provenance', run);
        if (run.metadata?.origin === 'legacy_import') notice(parent, 'Legacy prediction: original run provenance, candidate alternatives, and verification timing may be unknown. A single recorded candidate score does not mean the model evaluated only one candidate.');
    }
    else notice(parent, 'Run metadata is unavailable for this record. Legacy or partial exports cannot establish its model or original scoring context.');
    if (row.evidence_error) notice(parent, `Current evidence could not be fully exported: ${display(row.evidence_error)}`, 'danger');
    else if (!row.evidence) notice(parent, 'Current evidence is unavailable for this record. Candidate scores below may still be available.');
    const capped = [];
    if (row.candidates_truncated) capped.push('historical candidate scores');
    if (evidence.candidates_truncated) capped.push('original candidates');
    if (evidence.inputs_truncated) capped.push('transaction inputs');
    if (evidence.remaining_candidates_truncated) capped.push('remaining candidates');
    if (evidence.related_rings_truncated) capped.push('related rings');
    if (list(evidence.candidates).some(item => item.eliminated_by_truncated)) capped.push('elimination sources');
    if (capped.length) notice(parent, `Bounded evidence: ${capped.join(', ')} are truncated. An omitted item must not be interpreted as absent evidence.`);
    if (list(evidence.conflicts).length) {
        const conflictBox = element('div', 'notice danger');
        append(conflictBox, element('strong', '', 'Current evidence conflicts'), append(element('ul', 'conflict-list'), ...evidence.conflicts.map(item => element('li', '', item))));
        parent.append(conflictBox);
    }
    if (typeof window !== 'undefined' && window.TraceGroveEvidence) {
        const graph = element('section');
        parent.append(graph);
        window.TraceGroveEvidence.render(graph, row);
    }
    renderCandidates(parent, row, evidence);
    renderLineage(parent, evidence.lineage);
    if (Array.isArray(evidence.related_rings)) {
        const section = append(element('div', 'inspector-section'), element('h3', '', 'Related rings in the current snapshot'), element('p', '', 'Shared candidate outputs are structural links. They do not establish common wallet ownership or receiving-address groups.'));
        table(section, ['Key image', 'Shared candidate outputs'], evidence.related_rings.map(item => [element('code', '', display(item.key_image)), count(item.shared_outputs)]));
        if (!evidence.related_rings.length) section.append(element('p', '', 'No related rings included.'));
        parent.append(section);
    }
}

function renderCandidates(parent, row, evidence) {
    const section = append(element('div', 'inspector-section'), element('h3', '', 'Candidates and current eliminations'));
    const scores = list(row.candidates);
    const current = list(evidence.candidates);
    const identities = new Map();
    for (const item of [...current.map(candidate => candidate.output), ...scores]) {
        if (identity(item) !== 'Unknown') identities.set(identity(item), item);
    }
    const remaining = new Set(list(evidence.remaining_candidates).map(identity));
    const selected = identity(selectedOutput(row));
    const recordedCount = Array.isArray(row.candidates) ? scores.length : null;
    section.append(element('p', '', `${count(recordedCount)} of ${count(row.candidates_total ?? (row.candidates_truncated ? null : recordedCount))} recorded candidate scores included. Scores belong to the selected run. Only stored deterministic resolutions support the current elimination explanations below.`));
    table(section, ['Output (amount, index)', 'Recorded model score', 'Current candidate state', 'Supporting key images'], [...identities.entries()].map(([key, output]) => {
        const recorded = scores.find(item => sameOutput(item, output));
        const candidate = current.find(item => sameOutput(item.output, output));
        const sources = list(candidate?.eliminated_by);
        const sourceCount = candidate?.eliminated_by_count ?? sources.length;
        const sourceCell = element('div');
        let state = 'Unknown';
        if (candidate) {
            if (sourceCount > 0) state = 'Eliminated by deterministic evidence';
            else if (remaining.has(key)) state = 'Remaining';
            else if (deterministic(evidence.memory?.resolution) && !sameOutput(evidence.memory.resolution.output, output)) state = 'Excluded by stored ring resolution';
            else state = evidence.remaining_candidates_truncated ? 'Unknown · remaining list truncated' : Array.isArray(candidate.eliminated_by) ? 'No recorded external eliminator' : 'Unknown';
        }
        if (sources.length) sourceCell.append(append(element('ul', 'source-list'), ...sources.map(item => append(element('li'), element('code', '', display(item))))));
        else sourceCell.append(element('span', 'muted', candidate ? 'None included' : 'Unknown'));
        if (candidate?.eliminated_by_truncated) sourceCell.append(element('p', 'hint', `${count(sources.length)} of ${count(sourceCount)} sources included`));
        const outputCell = append(element('div'), element('code', '', key));
        if (key === selected) outputCell.append(element('span', 'secondary purple', 'Selected historical prediction'));
        return [outputCell, score(recorded?.score), state, sourceCell];
    }));
    if (!identities.size) section.append(element('p', '', 'Candidate identities and scores are unavailable in this export.'));
    parent.append(section);
}

function renderLineage(parent, lineage) {
    const section = append(element('div', 'inspector-section'), element('h3', '', 'Recorded resolution ancestry'), element('p', '', 'This trace follows the current stored resolution. Complete ancestry describes available records; it does not establish that a claim is deterministic.'));
    parent.append(section);
    if (!lineage) { notice(section, 'No resolution history is included. Its origin and dependencies are unknown.'); return; }
    const events = list(lineage.events);
    const legacy = events.some(event => event.method === 'legacy');
    const hypotheses = events.some(event => !deterministic(event.resolution));
    const flags = [];
    if (lineage.truncated) flags.push('Trace truncated at the export limit.');
    if (events.some(event => event.dependencies_truncated)) flags.push('Dependency lists are truncated; the displayed ancestry graph is partial even if all trace events are included.');
    if (legacy) flags.push('Legacy origins are unknown.');
    if (hypotheses) flags.push('Hypotheses contributed to at least one recorded event.');
    if (!lineage.complete) flags.push('Ancestry is incomplete or unavailable.');
    if (list(lineage.missing_event_ids).length) flags.push(`Missing events: ${lineage.missing_event_ids.map(display).join(', ')}.`);
    if (!known(lineage.root_event_id)) flags.push('No matching recorded root for the current resolution.');
    if (flags.length) notice(section, flags.join(' '));
    else section.append(element('p', 'hint', 'All traced dependency records are included and their origins are known.'));
    const eventAnchors = new Map(events.map((event, index) => [String(event.event_id), `trace-event-${index}`]));
    const traceList = element('ol', 'trace-list');
    for (const event of events) {
        const item = element('li', 'trace-event');
        item.id = eventAnchors.get(String(event.event_id));
        const title = append(element('p'), element('strong', '', `Event ${display(event.event_id)} · ${display(event.method)} `), element('span', `badge ${deterministic(event.resolution) ? 'correct' : 'purple'}`, deterministic(event.resolution) ? 'Deterministic' : 'Hypothesis'));
        append(item, title, element('p', 'mono', display(event.key_image)), element('p', '', `Claim ${identity(event.resolution?.output)} · score ${score(event.resolution?.confidence)} · scan ${count(event.scan_height)} · ${display(event.recorded_at)}`));
        const dependencies = list(event.dependencies);
        if (event.dependencies_truncated) item.append(element('p', 'yellow', `${count(dependencies.length)} of ${count(event.dependency_count)} dependencies included. Other supporting events are omitted from this list.`));
        if (dependencies.length) {
            const ul = element('ul');
            for (const dependency of dependencies) {
                const li = element('li', '', `Eliminated ${identity(dependency.eliminated_output)} using `);
                const anchor = eventAnchors.get(String(dependency.source_event_id));
                if (anchor) {
                    const link = element('a', '', `event ${display(dependency.source_event_id)}`);
                    link.href = `#${anchor}`;
                    li.append(link);
                } else li.append(document.createTextNode(`event ${display(dependency.source_event_id)} (outside this trace)`));
                ul.append(li);
            }
            item.append(ul);
        } else item.append(element('p', 'muted', event.method === 'legacy' ? 'Dependencies were not recorded for this legacy origin.' : 'No dependency events recorded.'));
        traceList.append(item);
    }
    section.append(traceList);
}

function chartCard(parent, title, description, headers, rows) {
    const card = element('section', 'card');
    heading(card, title, description);
    const holder = element('div', 'chart-container');
    const canvas = element('canvas');
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `${title}. Values are also available in the data table below.`);
    holder.append(canvas);
    card.append(holder);
    const alternative = append(element('details'), element('summary', '', 'View chart data as a table'));
    table(alternative, headers, rows, title);
    card.append(alternative);
    parent.append(card);
    if (typeof Chart === 'undefined') {
        holder.hidden = true;
        alternative.open = true;
    }
    return { canvas, holder, alternative };
}

function drawChart(target, config) {
    if (typeof Chart === 'undefined') { pendingCharts.push({ target, config }); return; }
    try {
        Chart.defaults.color = '#a1aebd';
        Chart.defaults.borderColor = '#303b49';
        new Chart(target.canvas, config);
    }
    catch { target.holder.hidden = true; target.alternative.open = true; }
}

function renderCharts(app, data) {
    const summary = data.summary || {};
    const section = element('div', 'charts-section');
    if (typeof Chart !== 'undefined') { Chart.defaults.color = '#a1aebd'; Chart.defaults.borderColor = '#303b49'; }
    else {
        const fallback = element('p', 'notice info charts-unavailable', 'Charts are loading or unavailable. The same exported values are shown in accessible tables below.');
        section.append(fallback);
    }
    const grid = element('div', 'charts-grid');
    const categories = evidenceCategories(summary);
    const entries = categories.complete ? categories.entries : [
        ['Stored resolutions · evidence kind unknown', summary.fully_resolved, 'gray'],
        ['Partially reduced · legacy count', summary.partially_reduced, 'yellow'],
        ['Unreduced · legacy count', summary.unreduced, 'blue'],
    ];
    const resolution = chartCard(grid, 'Evidence breakdown', 'Conflict flags overlap these categories and are excluded from the chart.', ['Evidence category', 'Rings', 'Share of all rings'], entries.map(([label, value]) => [label, count(value), percentage(value, summary.total_rings)]));
    const common = { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } };
    drawChart(resolution, { type: 'doughnut', data: { labels: entries.map(entry => entry[0]), datasets: [{ data: entries.map(entry => isNumber(entry[1]) ? entry[1] : null), backgroundColor: entries.map(entry => COLORS[entry[2]]), borderWidth: 0 }] }, options: { ...common, cutout: '65%' } });
    const distribution = summary.effective_ring_size_distribution || {};
    const partial = summary.partially_reduced_ring_size_distribution;
    const unchanged = summary.unreduced_ring_size_distribution;
    const sizes = [...new Set([...Object.keys(distribution), ...Object.keys(partial || {}), ...Object.keys(unchanged || {})])].sort((a, b) => Number(a) - Number(b));
    const splitKnown = categories.complete && summary.deterministic_resolutions + summary.hypothesis_resolutions === summary.fully_resolved;
    let sizeDatasets;
    if (partial && unchanged) {
        const resolvedDatasets = splitKnown ? [
            { label: 'Deterministic resolutions', value: summary.deterministic_resolutions, color: COLORS.green },
            { label: 'Hypothesis resolutions', value: summary.hypothesis_resolutions, color: COLORS.purple },
        ] : [{ label: 'Stored resolutions · kind unknown', value: summary.fully_resolved, color: COLORS.gray }];
        sizeDatasets = [
            ...resolvedDatasets.map(item => ({ label: item.label, data: sizes.map(size => size === '1' ? item.value ?? null : 0), backgroundColor: item.color })),
            { label: 'Unresolved · reduced', data: sizes.map(size => partial[size] ?? 0), backgroundColor: COLORS.yellow },
            { label: 'Unresolved · unchanged', data: sizes.map(size => unchanged[size] ?? 0), backgroundColor: COLORS.blue },
        ];
    } else sizeDatasets = [{ label: 'All rings · category split unavailable', data: sizes.map(size => distribution[size] ?? null), backgroundColor: COLORS.blue }];
    const ringChart = chartCard(grid, 'Effective ring sizes', 'Current analysis snapshot; reductions may include hypotheses. Size zero means no candidates remain, not a successful resolution.', ['Effective ring size', ...sizeDatasets.map(item => item.label)], sizes.map((size, index) => [size, ...sizeDatasets.map(item => count(item.data[index]))]));
    drawChart(ringChart, { type: 'bar', data: { labels: sizes, datasets: sizeDatasets }, options: { ...common, scales: { x: { stacked: true, title: { display: true, text: 'Effective ring size' } }, y: { stacked: true, beginAtZero: true } } } });
    section.append(grid);
    const cohorts = historyCohorts(data.history, data.scope?.dataset_id);
    const history = cohorts.current;
    if (history.length > 1) {
        const historyParent = element('div', 'charts-section');
        const historyChart = chartCard(historyParent, 'Stored resolution history', 'Snapshots from the current dataset only. Historical totals can include hypotheses. Rates use all rings in each snapshot.', ['Blocks scanned', 'Total rings', 'Stored resolution rate'], history.map(item => [count(item.blocks_scanned), count(item.total_rings), display(item.resolution_rate)]));
        drawChart(historyChart, { type: 'line', data: { labels: history.map(item => count(item.blocks_scanned)), datasets: [
            { label: 'Stored resolution rate %', data: history.map(item => Number.isFinite(parseFloat(item.resolution_rate)) ? parseFloat(item.resolution_rate) : null), borderColor: COLORS.orange, tension: .2 },
            { label: 'Total rings', data: history.map(item => item.total_rings ?? null), borderColor: COLORS.blue, yAxisID: 'y1', tension: .2 },
        ] }, options: { ...common, scales: { y: { beginAtZero: true, title: { display: true, text: 'Stored resolution %' } }, y1: { position: 'right', beginAtZero: true, grid: { drawOnChartArea: false }, title: { display: true, text: 'Total rings' } }, x: { title: { display: true, text: 'Blocks scanned' } } } } });
        section.append(historyParent);
    }
    if (cohorts.unknown.length || cohorts.other.length || history.length === 1) {
        const historyCard = element('section', 'card charts-section');
        heading(historyCard, 'Additional historical snapshots', 'Unknown or different dataset identities are kept separate from the current dataset timeline. Historical resolution totals may include hypotheses.');
        for (const [label, snapshots] of [
            ['Current dataset · one snapshot', history.length === 1 ? history : []],
            ['Historical snapshots · dataset unknown', cohorts.unknown],
            ['Historical snapshots · other datasets', cohorts.other],
        ]) {
            if (!snapshots.length) continue;
            const group = append(element('details'), element('summary', '', `${label} (${snapshots.length})`));
            table(group, ['Dataset', 'Blocks scanned', 'Total rings', 'Stored resolution rate'], snapshots.map(item => [display(item.dataset_id), count(item.blocks_scanned), count(item.total_rings), display(item.resolution_rate)]), label);
            historyCard.append(group);
        }
        section.append(historyCard);
    }
    app.append(section);
}

function renderQuality(app, data) {
    const ml = data.ml_predictions;
    if (ml && typeof ml === 'object') {
        const section = element('section', 'card');
        heading(section, 'Latest accepted prediction verification', 'One latest accepted prediction per ring. These aggregate counts differ from the historical scoring records in the browser.');
        const metrics = element('div', 'metrics');
        for (const [label, value, color] of [
            ['Latest predictions', count(ml.total_predictions), ''], ['Pending', count(ml.unverified), 'yellow'], ['Verified', count(ml.verified), ''],
            ['Correct', count(ml.correct), 'green'], ['Wrong', count(ml.wrong), 'red'], ['Verified accuracy', percentage(ml.correct, ml.verified), 'orange'],
        ]) metrics.append(append(element('div', 'metric'), element('strong', color, value), element('span', '', label)));
        append(section, metrics, element('p', 'hint', `Verified fraction: ${percentage(ml.verified, ml.total_predictions)} of latest accepted predictions. Accuracy is correct / verified; pending predictions do not enter that denominator. Verification checks agreement with stored deterministic evidence, whose validity depends on analysis assumptions. The verified subset may be selective.`));
        app.append(section);
    }
    const training = data.ml_training;
    if (!training || typeof training !== 'object') return;
    const section = element('section', 'card');
    heading(section, 'Training holdout', 'Ring-level holdout performance is separate from later verification and does not establish forward-time accuracy.');
    const metrics = element('div', 'metrics');
    for (const [label, value] of [
        ['Holdout accuracy', percentage(training.holdout_correct, training.holdout_rings)], ['Correct holdout rings', count(training.holdout_correct)],
        ['Holdout rings', count(training.holdout_rings)], ['Training rings', count(training.training_rings)],
    ]) metrics.append(append(element('div', 'metric'), element('strong', '', value), element('span', '', label)));
    section.append(metrics);
    const features = list(training.feature_importances).slice(0, 12).reverse();
    if (features.length) {
        const container = element('div', 'charts-section');
        const target = chartCard(container, 'Feature importances', 'Up to 12 leading features from the exported training run.', ['Feature', 'Importance'], features.map(item => [display(item.feature), score(item.importance)]));
        drawChart(target, { type: 'bar', data: { labels: features.map(item => item.feature), datasets: [{ label: 'Importance', data: features.map(item => item.importance), backgroundColor: COLORS.purple }] }, options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true } } } });
        section.append(container);
    }
    app.append(section);
}

function renderDashboard(data) {
    const app = document.getElementById('app');
    app.replaceChildren();
    renderSummary(app, data);
    const analytics = element('section', 'card');
    analytics.id = 'analytics-lab';
    analytics.tabIndex = -1;
    app.append(analytics);
    const inspect = renderBrowser(app, data);
    if (window.TraceGroveAnalytics) {
        try { window.TraceGroveAnalytics.mount(analytics, data, {onInspect: inspect}); }
        catch (error) { notice(analytics, `Analytics could not be displayed: ${display(error.message)}`); }
    } else notice(analytics, 'The analytics module is unavailable. The prediction browser and raw export remain available.');
    renderQuality(app, data);
    renderCharts(app, data);
    const footer = element('p', 'timestamp', `Export snapshot: ${display(data.scope?.exported_at || data.generated_at)} · `);
    const raw = element('a', '', 'Download JSON');
    raw.href = 'data.json';
    raw.download = 'data.json';
    footer.append(raw);
    app.append(footer);
    app.setAttribute('aria-busy', 'false');
}

async function boot() {
    try {
        const response = await fetch('data.json');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        if (!data || typeof data !== 'object' || !data.summary || typeof data.summary !== 'object') throw new Error('The export is missing its summary object');
        renderDashboard(data);
    } catch (error) {
        const app = document.getElementById('app');
        app.replaceChildren();
        const box = element('div', 'empty-state');
        box.setAttribute('role', 'alert');
        append(box, element('h2', '', 'Analysis export could not be loaded'), element('p', '', display(error.message)), element('p', '', 'Generate docs/data.json with python main.py export-viz, then serve the docs directory over HTTP.'));
        const retry = element('button', '', 'Retry loading');
        retry.addEventListener('click', () => { app.setAttribute('aria-busy', 'true'); boot(); });
        append(app, append(box, retry));
        app.setAttribute('aria-busy', 'false');
    }
}

if (typeof module !== 'undefined' && module.exports) module.exports = { outcome, acceptance, evidenceCategories, historyCohorts, filterRows, identity, sameOutput, percentage };
if (typeof document !== 'undefined') {
    const featureAudit = document.getElementById('feature-observatory');
    if (featureAudit && window.TraceGroveFeatureAudit) window.TraceGroveFeatureAudit.mount(featureAudit);
    else if (featureAudit) {
        featureAudit.replaceChildren(element('p', 'notice', 'The feature audit module is unavailable. Reload the page to try again.'));
        featureAudit.setAttribute('aria-busy', 'false');
    }
    const knowledge = document.getElementById('knowledge-brain');
    if (knowledge && window.TraceGroveBrain) {
        Promise.resolve(window.TraceGroveBrain.mount(knowledge, {url: 'brain.json'})).catch(error => {
            knowledge.replaceChildren(element('p', 'notice', `Research brain could not be loaded: ${display(error.message)}`));
            knowledge.setAttribute('aria-busy', 'false');
        });
    } else if (knowledge) {
        knowledge.replaceChildren(element('p', 'notice', 'The research brain module is unavailable. Reload the page to try again.'));
        knowledge.setAttribute('aria-busy', 'false');
    }
    document.getElementById('chart-library')?.addEventListener('load', () => {
        if (typeof Chart === 'undefined') return;
        for (const { target, config } of pendingCharts.splice(0)) {
            if (!target.canvas.isConnected) continue;
            target.holder.hidden = false;
            target.alternative.open = false;
            drawChart(target, config);
        }
        document.querySelectorAll('.charts-unavailable').forEach(node => node.remove());
    });
    boot();
}
