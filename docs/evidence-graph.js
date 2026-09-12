(function (root) {
    'use strict';
    const key = output => output && output.amount != null && output.index != null ? JSON.stringify([String(output.amount), String(output.index)]) : null;
    const items = value => Array.isArray(value) ? value : [];
    const short = value => String(value ?? 'Unknown').length > 16 ? `${String(value).slice(0, 8)}…${String(value).slice(-5)}` : String(value ?? 'Unknown');
    const isDeterministic = value => value?.confidence === 1 && Number.isFinite(value?.pass_num) && value.pass_num >= 0;

    function buildGraph(row, options = {}) {
        const maxCandidates = Math.min(32, Math.max(1, options.maxCandidates || 10));
        const maxRelated = Math.min(12, Math.max(0, options.maxRelated ?? 5));
        const hypotheses = options.showHypotheses !== false;
        const evidence = row.evidence || {}, memory = evidence.memory || {};
        const nodes = [], edges = [], notes = [];
        if (!row.evidence) return { nodes, edges, notes: ['Current evidence is unavailable for this record.'] };
        const ring = `ring:${row.key_image}`;
        nodes.push({id: ring, kind: 'ring', label: 'Selected ring', detail: String(row.key_image), x: 150, y: 240});
        const transactions = [...new Set(items(memory.inputs).map(item => item.tx_hash).filter(Boolean))];
        transactions.slice(0, 2).forEach((hash, i) => {
            const id = `tx:${hash}`;
            nodes.push({id, kind: 'transaction', label: 'Transaction', detail: hash, x: 150, y: 60 + 65 * i});
            edges.push({source: id, target: ring, kind: 'input', label: 'Contains input'});
        });
        const predicted = {amount: row.predicted_amount, index: row.predicted_output_index};
        const priority = new Set([key(predicted), key(memory.resolution?.output)]);
        const unique = new Map(items(memory.members).filter(item => key(item)).map(item => [key(item), item]));
        const selected = [...unique.values()].sort((a, b) => Number(priority.has(key(b))) - Number(priority.has(key(a)))).slice(0, maxCandidates);
        const candidateMap = new Map(items(evidence.candidates).map(item => [key(item.output), item]));
        const remaining = new Set(items(evidence.remaining_candidates).map(key));
        const candidateTotal = Number.isFinite(evidence.candidate_count) ? evidence.candidate_count : unique.size;
        selected.forEach((output, i) => {
            const id = `output:${key(output)}`, candidate = candidateMap.get(key(output));
            const eliminators = candidate?.eliminated_by_count ?? items(candidate?.eliminated_by).length;
            const state = eliminators > 0 ? `Excluded by ${eliminators} recorded deterministic claim(s)`
                : remaining.has(key(output)) ? 'Compatible with current stored evidence'
                : evidence.remaining_candidates_truncated ? 'Survival status outside the bounded export is unknown'
                : 'Not in the current surviving candidate set';
            nodes.push({id, kind: eliminators > 0 ? 'excluded' : 'output', label: `Output ${short(output.index)}`,
                detail: `Amount ${output.amount} · index ${output.index}. ${state}.`, output, x: 500, y: 48 + 49 * i});
            edges.push({source: ring, target: id, kind: 'membership', label: 'Ring membership'});
            if (key(memory.resolution?.output) === key(output)) {
                if (isDeterministic(memory.resolution)) edges.push({source: ring, target: id, kind: 'deterministic', label: 'Current deterministic spend claim'});
                else if (hypotheses) edges.push({source: ring, target: id, kind: 'hypothesis', label: 'Current hypothesis spend claim'});
            }
            if (hypotheses && key(predicted) === key(output)) edges.push({source: ring, target: id, kind: 'prediction', label: 'Selected historical prediction'});
        });
        const related = items(evidence.related_rings).slice(0, maxRelated);
        related.forEach((other, i) => {
            const id = `related:${other.key_image}`;
            nodes.push({id, kind: 'related', label: `${other.shared_outputs} shared candidate(s)`, detail: `Related ring ${other.key_image}. Shared membership is not an ownership link.`, x: 850, y: 90 + i * 75});
            edges.push({source: ring, target: id, kind: 'overlap', label: `${other.shared_outputs} shared candidate(s)`});
        });
        notes.push(`Showing ${selected.length} of ${candidateTotal} original candidates and ${related.length} related rings. Output identity always includes amount and index.`);
        if (candidateTotal > selected.length || evidence.candidates_truncated || items(evidence.related_rings).length > related.length || evidence.related_rings_truncated || transactions.length > 2 || evidence.inputs_truncated) notes.push('This is a bounded neighborhood, not the complete graph. Hidden nodes are not evidence of absent relationships.');
        if (key(predicted) && !unique.has(key(predicted))) notes.push('The historical prediction is outside the exported membership list; no candidate link is invented for it.');
        notes.push('Membership and overlap are observations. Spend claims remain conditional on data and analysis assumptions. Historical predictions and current evidence refer to different times.');
        return {nodes, edges, notes, height: Math.max(450, 80 + selected.length * 49)};
    }

    function render(host, row) {
        host.replaceChildren();
        host.classList.add('evidence-network');
        const element = (tag, text, cls) => { const item = document.createElement(tag); if (text != null) item.textContent = text; if (cls) item.className = cls; return item; };
        host.append(element('h3', 'Local evidence graph'), element('p', 'Explore the selected ring, its candidates, and observed overlap. Select any node for exact details.', 'hint'));
        const controls = element('div', null, 'eg-controls');
        const label = element('label'), toggle = element('input'); toggle.type = 'checkbox'; toggle.checked = true;
        label.append(toggle, document.createTextNode(' Show hypotheses and historical prediction'));
        const countLabel = element('label', 'Candidates shown '), limit = element('select');
        limit.setAttribute('aria-label', 'Graph candidate limit');
        [10, 20, 32].forEach(n => { const option = element('option', n); option.value = n; limit.append(option); });
        countLabel.append(limit); controls.append(label, countLabel); host.append(controls);
        const legend = element('div', null, 'eg-legend');
        for (const [kind, text] of [['input', 'Transaction input'], ['membership', 'Candidate membership'], ['overlap', 'Observed overlap'], ['deterministic', 'Current deterministic claim'], ['hypothesis', 'Current hypothesis'], ['prediction', 'Historical prediction']]) {
            const entry = element('span', text, `eg-legend-${kind}`); legend.append(entry);
        }
        host.append(legend);
        const graphHost = element('div', null, 'eg-viewport'); graphHost.tabIndex = 0; graphHost.setAttribute('aria-label', 'Scrollable local evidence graph');
        const detail = element('p', 'Select a node to inspect its exact identifier.', 'eg-detail'); detail.setAttribute('role', 'status');
        const notes = element('div'), alternative = element('details'); alternative.append(element('summary', 'Graph connections as a table'));
        host.append(graphHost, detail, notes, alternative);
        const svgElement = (tag, attrs = {}) => { const item = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [name, value] of Object.entries(attrs)) item.setAttribute(name, value); return item; };
        function draw() {
            const graph = buildGraph(row, {maxCandidates: Number(limit.value), showHypotheses: toggle.checked});
            graphHost.replaceChildren(); notes.replaceChildren();
            graph.notes.forEach(note => notes.append(element('p', note, 'hint')));
            const svg = svgElement('svg', {viewBox: `0 0 1000 ${graph.height || 450}`, role: 'group', 'aria-label': 'Ring evidence network'});
            const positions = new Map(graph.nodes.map(node => [node.id, node]));
            graph.edges.forEach(edge => {
                const a = positions.get(edge.source), b = positions.get(edge.target);
                const offset = edge.kind === 'prediction' ? 7 : edge.kind === 'deterministic' || edge.kind === 'hypothesis' ? -6 : 0;
                const line = svgElement('path', {d: `M ${a.x} ${a.y + offset} C ${(a.x + b.x) / 2} ${a.y + offset}, ${(a.x + b.x) / 2} ${b.y + offset}, ${b.x} ${b.y + offset}`, class: `eg-edge eg-${edge.kind}`});
                const title = svgElement('title'); title.textContent = edge.label; line.append(title); svg.append(line);
            });
            graph.nodes.forEach(node => {
                const item = svgElement('g', {class: `eg-node eg-${node.kind}`, tabindex: '0', role: 'button', 'aria-label': `${node.label}. ${node.detail}`, transform: `translate(${node.x},${node.y})`});
                item.append(svgElement('rect', {x: -112, y: -22, width: 224, height: 44, rx: 9}));
                const title = svgElement('text', {x: 0, y: -2, 'text-anchor': 'middle'}); title.textContent = node.label;
                const subtitle = svgElement('text', {x: 0, y: 13, 'text-anchor': 'middle', class: 'eg-node-subtitle'}); subtitle.textContent = node.output ? `amount ${short(node.output.amount)}` : short(node.id.split(':').slice(1).join(':'));
                item.append(title, subtitle);
                const select = () => { detail.textContent = `${node.label}: ${node.detail}`; svg.querySelectorAll('[aria-pressed]').forEach(other => other.setAttribute('aria-pressed', 'false')); item.setAttribute('aria-pressed', 'true'); };
                item.setAttribute('aria-pressed', 'false'); item.addEventListener('click', select); item.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); } });
                svg.append(item);
            });
            graphHost.append(svg);
            while (alternative.children.length > 1) alternative.lastChild.remove();
            const table = element('table'), caption = element('caption', 'Displayed connections only; overlap does not establish ownership.'); table.append(caption);
            const headers = element('tr'); ['From', 'Relationship', 'To'].forEach(text => headers.append(element('th', text))); table.append(headers);
            graph.edges.forEach(edge => { const tr = element('tr'); [positions.get(edge.source).detail, edge.label, positions.get(edge.target).detail].forEach(text => tr.append(element('td', text))); table.append(tr); });
            const scroll = element('div', null, 'table-scroll'); scroll.append(table); alternative.append(scroll);
        }
        toggle.addEventListener('change', draw); limit.addEventListener('change', draw); draw();
    }
    root.TraceGroveEvidence = {buildGraph, render};
    if (typeof module !== 'undefined' && module.exports) module.exports = {buildGraph, key};
})(typeof window !== 'undefined' ? window : globalThis);
