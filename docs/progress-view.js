(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.TraceGroveProgress = api;
}(typeof globalThis === 'object' ? globalThis : this, function () {
    'use strict';
    const array = value => Array.isArray(value) ? value : [];
    const present = value => typeof value === 'string' && value.trim().length > 0;
    const count = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
    const fmt = value => count(value) === null ? 'Unknown' : value.toLocaleString();
    const signed = value => value === null ? 'No comparable prior snapshot' : `${value > 0 ? '+' : ''}${value.toLocaleString()}`;
    const short = value => String(value).length > 22 ? `${String(value).slice(0, 10)}…${String(value).slice(-7)}` : String(value);
    const percent = (part, total) => part !== null && total > 0 ? `${(100 * part / total).toFixed(2)}%` : 'Unknown';
    const mounts = new WeakMap();

    function outputKey(output) {
        const decimal = value => typeof value === 'string' && /^(0|[1-9][0-9]*)$/.test(value)
            ? value : count(value) !== null ? String(value) : null;
        const amount = decimal(output?.amount), index = decimal(output?.index);
        return amount !== null && index !== null ? JSON.stringify([amount, index]) : null;
    }

    function observedTime(value) {
        if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
        const time = Date.parse(value);
        return Number.isFinite(time) ? time : null;
    }

    function timeline(data) {
        const datasetId = present(data?.scope?.dataset_id) ? data.scope.dataset_id : null;
        const exclusions = {otherDataset: 0, unknownDataset: 0, invalidTimestamp: 0, duplicateSnapshot: 0, conflictingTimestamp: 0};
        const byTime = new Map(), conflicts = new Set();
        function snapshot(row, timestamp) {
            const total = count(row.total_rings), resolved = count(row.deterministic_resolutions);
            const point = {timestamp, observedAt: new Date(timestamp).toISOString(), total,
                deterministic: total !== null && resolved !== null && resolved <= total ? resolved : null,
                blocksScanned: count(row.blocks_scanned)};
            for (const [target, source] of [['originalSingleton', 'original_singleton_rings'], ['originalMultimember', 'original_multimember_rings'],
                ['deterministicSingleton', 'deterministic_singleton_resolutions'], ['deterministicMultimember', 'deterministic_multimember_resolutions']]) {
                const value = count(row[source]); point[target] = total !== null && value !== null && value <= total ? value : null;
            }
            if (point.originalSingleton !== null && point.originalMultimember !== null && point.originalSingleton + point.originalMultimember !== total) {
                point.originalSingleton = point.originalMultimember = null;
            }
            for (const [field, original] of [['deterministicSingleton', 'originalSingleton'], ['deterministicMultimember', 'originalMultimember']]) {
                if (point.deterministic === null || point[field] > point.deterministic || point[original] !== null && point[field] > point[original]) point[field] = null;
            }
            if (point.deterministicSingleton !== null && point.deterministicMultimember !== null && point.deterministicSingleton + point.deterministicMultimember !== point.deterministic) {
                point.deterministicSingleton = point.deterministicMultimember = null;
            }
            return point;
        }
        for (const row of array(data?.history)) {
            if (!present(row?.dataset_id)) { exclusions.unknownDataset++; continue; }
            if (datasetId === null || row.dataset_id !== datasetId) { exclusions.otherDataset++; continue; }
            const time = observedTime(row.timestamp);
            if (time === null) { exclusions.invalidTimestamp++; continue; }
            const point = snapshot(row, time);
            if (byTime.has(time)) {
                if (JSON.stringify(byTime.get(time)) === JSON.stringify(point)) exclusions.duplicateSnapshot++;
                else conflicts.add(time);
            } else byTime.set(time, point);
        }
        conflicts.forEach(time => { byTime.delete(time); exclusions.conflictingTimestamp++; });
        const points = [...byTime.values()].sort((a, b) => a.timestamp - b.timestamp);
        // A current export may have no history row yet. Add it only with explicit dataset/time.
        const currentTime = observedTime(data?.scope?.exported_at);
        if (datasetId && currentTime !== null && data?.summary && !conflicts.has(currentTime)) {
            const current = snapshot(data.summary, currentTime), last = points.at(-1);
            if (!last || currentTime > last.timestamp && ['total', 'deterministic', 'blocksScanned', 'originalSingleton', 'originalMultimember', 'deterministicSingleton', 'deterministicMultimember'].some(key => current[key] !== last[key])) points.push(current);
        }
        points.forEach((point, index) => {
            const previous = points[index - 1];
            point.delta = previous && point.deterministic !== null && previous.deterministic !== null
                ? point.deterministic - previous.deterministic : null;
            point.totalDelta = previous && point.total !== null && previous.total !== null ? point.total - previous.total : null;
            point.multimemberDelta = previous && point.deterministicMultimember !== null && previous.deterministicMultimember !== null
                ? point.deterministicMultimember - previous.deterministicMultimember : null;
        });
        return {datasetId, points, exclusions};
    }

    function relationships(data) {
        const rows = array(data?.prediction_browser?.rows), rings = new Map(), duplicateRings = new Set();
        let missingRingRecords = 0;
        for (const row of rows) {
            if (!present(row?.key_image)) { missingRingRecords++; continue; }
            if (rings.has(row.key_image)) duplicateRings.add(row.key_image);
            else rings.set(row.key_image, row);
        }
        const transactions = new Map(), outputs = new Map(), pairs = new Map();
        let missingEvidence = 0, boundedMemberships = 0, boundedInputs = 0, boundedRelated = 0, invalidOutputs = 0;
        const add = (map, key, ring) => { if (!map.has(key)) map.set(key, new Set()); map.get(key).add(ring); };
        for (const [ring, row] of rings) {
            const evidence = row.evidence, memory = evidence?.memory;
            if (!memory) missingEvidence++;
            if (evidence?.candidates_truncated || count(evidence?.candidate_count) > array(memory?.members).length) boundedMemberships++;
            if (evidence?.inputs_truncated || count(evidence?.input_count) > array(memory?.inputs).length) boundedInputs++;
            if (evidence?.related_rings_truncated) boundedRelated++;
            for (const input of array(memory?.inputs)) if (present(input?.tx_hash)) add(transactions, input.tx_hash, ring);
            for (const output of array(memory?.members)) {
                const key = outputKey(output);
                if (key === null) invalidOutputs++;
                else add(outputs, key, ring);
            }
            for (const related of array(evidence?.related_rings)) {
                if (!present(related?.key_image) || related.key_image === ring || count(related.shared_outputs) === null || related.shared_outputs < 1) continue;
                const members = [ring, related.key_image].sort(), key = JSON.stringify(members);
                if (!pairs.has(key)) pairs.set(key, {id: `related:${key}`, type: 'related', key, members, counts: new Set(), reporters: new Set()});
                pairs.get(key).counts.add(related.shared_outputs); pairs.get(key).reporters.add(ring);
            }
        }
        const groups = (map, type) => [...map].filter(([, members]) => members.size > 1).map(([key, members]) => ({
            id: `${type}:${key}`, type, key, members: [...members].sort(), output: type === 'output' ? JSON.parse(key) : null,
        })).sort((a, b) => b.members.length - a.members.length || a.key.localeCompare(b.key));
        const reported = [...pairs.values()].map(value => ({...value, counts: [...value.counts].sort((a, b) => a - b), reporters: [...value.reporters].sort()}))
            .sort((a, b) => b.counts[0] - a.counts[0] || a.key.localeCompare(b.key));
        const external = new Set(reported.flatMap(value => value.members).filter(ring => !rings.has(ring)));
        return {rings, transactions: groups(transactions, 'transaction'), outputs: groups(outputs, 'output'), related: reported,
            scope: {records: rows.length, totalRecords: count(data?.prediction_browser?.total), uniqueRings: rings.size,
                repeatedRecords: rows.length - rings.size - missingRingRecords, duplicateRings: duplicateRings.size, missingRingRecords,
                missingEvidence, boundedMemberships, boundedInputs, boundedRelated, invalidOutputs, externalNeighbors: external.size,
                limit: count(data?.prediction_browser?.limit), order: data?.prediction_browser?.order}};
    }

    function mount(host, initialData, hooks = {}) {
        if (!host?.ownerDocument) throw new TypeError('A DOM host is required');
        mounts.get(host)?.destroy();
        const document = host.ownerDocument;
        let data = initialData, report, selected = null, filter = 'transaction', destroyed = false;
        const el = (tag, text, className) => { const node = document.createElement(tag); if (text != null) node.textContent = String(text); if (className) node.className = className; return node; };
        const svgEl = (tag, attrs = {}) => { const node = document.createElementNS('http://www.w3.org/2000/svg', tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value))); return node; };
        const append = (node, ...children) => { node.append(...children); return node; };
        const button = (text, action) => { const node = el('button', text); node.type = 'button'; node.addEventListener('click', action); return node; };
        const shell = el('section', null, 'tg-progress'); shell.setAttribute('aria-label', 'Collection progress and ring relationships');
        host.replaceChildren(shell);

        function table(parent, caption, headers, values) {
            const wrap = el('div', null, 'tp-table-wrap'); wrap.tabIndex = 0; wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', caption);
            const table = el('table'), head = el('tr'), body = el('tbody'); table.append(el('caption', caption));
            headers.forEach(name => { const th = el('th', name); th.scope = 'col'; head.append(th); });
            values.forEach(values => { const row = el('tr'); values.forEach(value => row.append(append(el('td'), value?.nodeType ? value : document.createTextNode(String(value))))); body.append(row); });
            table.append(append(el('thead'), head), body); wrap.append(table); parent.append(wrap);
        }

        function drawTimeline(parent, points) {
            if (!points.length) { parent.append(el('p', 'No timestamped snapshots with a matching dataset identity are available.', 'tp-note')); return; }
            const width = 900, height = 240, left = 78, right = 25, top = 20, bottom = 45;
            const maximum = Math.max(1, ...points.map(point => point.total || 0));
            const start = points[0].timestamp, span = points.at(-1).timestamp - start;
            const x = point => left + (span ? (point.timestamp - start) / span : .5) * (width - left - right);
            const y = value => height - bottom - value / maximum * (height - top - bottom);
            const svg = svgEl('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': 'Snapshot totals by export observation time in UTC. Exact values follow in the table.', class: 'tp-timeline'});
            const title = svgEl('title'); title.textContent = 'Current dataset: deterministic labels and all rings at export observations'; svg.append(title);
            for (const fraction of [0, .5, 1]) {
                const value = fraction * maximum;
                svg.append(svgEl('line', {x1: left, x2: width - right, y1: y(value), y2: y(value), class: 'tp-grid'}));
                const label = svgEl('text', {x: left - 10, y: y(value) + 4, 'text-anchor': 'end'}); label.textContent = Math.round(value).toLocaleString(); svg.append(label);
            }
            for (const [field, className] of [['total', 'tp-total'], ['deterministic', 'tp-deterministic']]) {
                let path = '', connected = false;
                for (const point of points) {
                    if (point[field] === null) { connected = false; continue; }
                    path += `${connected ? 'L' : 'M'}${x(point)},${y(point[field])} `; connected = true;
                    const circle = svgEl('circle', {cx: x(point), cy: y(point[field]), r: 4, class: className});
                    const tooltip = svgEl('title'); tooltip.textContent = `${point.observedAt}: ${field} ${fmt(point[field])}`; circle.append(tooltip); svg.append(circle);
                }
                svg.append(svgEl('path', {d: path, class: className, fill: 'none'}));
            }
            for (const [point, anchor] of [[points[0], 'start'], [points.at(-1), 'end']]) {
                const label = svgEl('text', {x: x(point), y: height - 15, 'text-anchor': anchor}); label.textContent = point.observedAt.replace('T', ' ').slice(5, 16) + ' UTC'; svg.append(label);
            }
            parent.append(svg, el('p', 'Teal: deterministic labels · slate: all rings · vertical axis starts at zero.', 'tp-note'));
        }

        function render() {
            if (destroyed) return;
            report = {timeline: timeline(data), relationships: relationships(data)};
            const history = report.timeline, rel = report.relationships, latest = history.points.at(-1);
            shell.replaceChildren();
            shell.append(el('p', 'MEASURED PROGRESS', 'tp-kicker'), el('h2', 'Resolution progress & relationships'),
                el('p', 'Track changes between exports, then inspect shared candidate outputs and transaction inputs.', 'tp-intro'));
            const metrics = el('div', null, 'tp-metrics');
            for (const [label, value, note] of [
                ['Deterministic labels, including singletons', fmt(latest?.deterministic), `${fmt(latest?.total)} total rings · ${percent(latest?.deterministic ?? null, latest?.total)}`],
                ['Net change since prior export', latest ? signed(latest.delta) : 'Unknown', latest?.totalDelta !== null && latest?.totalDelta !== undefined ? `Change in total rings: ${signed(latest.totalDelta)}` : 'Two comparable observations required'],
                ['Comparable observations', fmt(history.points.length), 'Current dataset only'],
                ['Original singleton rings', fmt(latest?.originalSingleton), latest?.originalSingleton == null ? 'Original-size breakdown unavailable' : 'One original candidate; no ambiguity to reduce'],
                ['Deterministic multi-member rings', fmt(latest?.deterministicMultimember), `${fmt(latest?.originalMultimember)} original multi-member rings · ${percent(latest?.deterministicMultimember ?? null, latest?.originalMultimember)}`],
                ['Net multi-member labels since prior', latest ? signed(latest.multimemberDelta) : 'Unknown', 'Requires the original-size breakdown in both snapshots'],
            ]) metrics.append(append(el('div'), el('span', label), el('strong', value), el('small', note)));
            shell.append(metrics);
            shell.append(el('p', 'Totals include original singleton rings. The original-size breakdown is shown only when explicitly exported; older snapshots remain unknown. A rising total can reflect newly scanned rings, including singletons, and analysis of earlier rings. Net change is not a count of independently discovered links or a measured gain in deanonymization.', 'tp-note'));
            const progress = el('section', null, 'tp-panel'); progress.append(el('h3', 'Resolved rings over export observations'));
            progress.append(el('p', `Dataset: ${history.datasetId || 'unknown'}. Dates are export/analysis observation timestamps in UTC, not resolution-event times or blockchain dates. Negative changes are retained as corrections.`, 'tp-note'));
            drawTimeline(progress, history.points);
            const detail = append(el('details'), el('summary', 'Exact snapshot counts and changes'));
            table(detail, 'Current-dataset snapshots, ordered by observation time', ['Observed at (UTC)', 'Blocks scanned', 'Deterministic / all rings', 'Rate', 'Net new labels', 'Net new rings', 'Deterministic singleton', 'Deterministic multi-member', 'Net new multi-member labels'], history.points.map(point => [
                point.observedAt, fmt(point.blocksScanned), `${fmt(point.deterministic)} / ${fmt(point.total)}`, percent(point.deterministic, point.total), signed(point.delta), signed(point.totalDelta), fmt(point.deterministicSingleton), fmt(point.deterministicMultimember), signed(point.multimemberDelta)]));
            const excluded = history.exclusions;
            detail.append(el('p', `Excluded from comparisons: ${excluded.unknownDataset} unknown-dataset rows; ${excluded.otherDataset} other-dataset rows; ${excluded.invalidTimestamp} invalid timestamps; ${excluded.duplicateSnapshot} duplicate snapshots; ${excluded.conflictingTimestamp} conflicting observation times. Historical stored totals are never substituted for missing deterministic totals.`, 'tp-note'));
            progress.append(detail); shell.append(progress);

            const groups = el('section', null, 'tp-panel'); groups.append(el('h3', 'Explore ring groupings'));
            groups.append(el('p', `${fmt(rel.scope.records)} exported scoring records (${rel.scope.order === 'newest_first' ? 'newest first' : 'ordering unknown'}) of ${fmt(rel.scope.totalRecords)}; ${fmt(rel.scope.uniqueRings)} distinct rings. ${fmt(rel.scope.repeatedRecords)} repeated records are deduplicated by key image, retaining the first included record for inspection.`, 'tp-note'));
            groups.append(el('p', 'These are output, ring, and transaction relationships in current exported evidence. Shared decoy candidates and inputs in the same transaction do not establish wallet or address ownership. Groups overlap and are not merged transitively.', 'tp-boundary'));
            const groupMetrics = el('div', null, 'tp-group-metrics');
            for (const [value, label] of [[rel.transactions.length, 'multi-ring transactions'], [rel.outputs.length, 'shared-output groups'], [rel.related.length, 'reported ring pairs']]) groupMetrics.append(append(el('div'), el('strong', fmt(value)), el('span', label)));
            groups.append(groupMetrics);
            const controls = el('div', null, 'tp-controls');
            for (const [value, label] of [['transaction', 'Same transaction'], ['output', 'Shared output'], ['related', 'Reported overlap']]) {
                const control = button(label, () => { filter = value; selected = null; renderGroups(); }); control.dataset.kind = value; controls.append(control);
            }
            const groupBody = el('div', null, 'tp-groups'), listHost = el('div', null, 'tp-group-list'), selectedHost = el('article', null, 'tp-selected');
            selectedHost.tabIndex = -1; groupBody.append(listHost, selectedHost); groups.append(controls, groupBody);
            const status = el('p', null, 'tp-note'); status.setAttribute('role', 'status'); groups.append(status);
            groups.append(el('p', `Evidence limits: ${rel.scope.boundedMemberships} rings have truncated memberships, ${rel.scope.boundedInputs} truncated input lists, and ${rel.scope.boundedRelated} truncated related-ring lists; ${rel.scope.missingEvidence} lack memory details. ${rel.scope.invalidOutputs} malformed or unsafe numeric output identities were ignored. Reported pairs include ${rel.scope.externalNeighbors} distinct neighbors outside the exported records. Missing edges remain unknown.`, 'tp-note'));
            shell.append(groups);

            function groupTitle(group) {
                if (group.type === 'transaction') return `Transaction ${short(group.key)}`;
                if (group.type === 'output') return `Output ${short(group.output[0])} : ${short(group.output[1])}`;
                return `${short(group.members[0])} ↔ ${short(group.members[1])}`;
            }
            function renderGroups() {
                const available = filter === 'transaction' ? rel.transactions : filter === 'output' ? rel.outputs : rel.related;
                const visible = available.slice(0, 24);
                if (!visible.some(group => group.id === selected)) selected = visible[0]?.id || null;
                [...controls.children].forEach(control => control.setAttribute('aria-pressed', String(control.dataset.kind === filter)));
                listHost.replaceChildren(); selectedHost.replaceChildren();
                status.textContent = `Showing ${visible.length} of ${available.length} direct ${filter === 'related' ? 'reported overlap pairs, ordered by reported shared count' : 'groups, ordered by number of included rings'}. Counts describe this bounded export only.`;
                for (const group of visible) {
                    const item = button('', () => { selected = group.id; renderGroups(); selectedHost.focus({preventScroll: true}); }); item.className = 'tp-group-button'; item.setAttribute('aria-pressed', String(group.id === selected));
                    const subtitle = group.type === 'related' ? `${group.counts.join(' / ')} reported shared candidate(s)` : `${group.members.length} included rings`;
                    item.append(el('strong', groupTitle(group)), el('span', subtitle)); listHost.append(item);
                }
                const group = visible.find(group => group.id === selected);
                if (!group) { selectedHost.append(el('p', 'No direct groups of this kind are visible in the bounded export. This does not establish that none exist in the full graph.')); return; }
                selectedHost.append(el('p', 'SELECTED RELATIONSHIP', 'tp-kicker'), el('h4', groupTitle(group)));
                if (group.type === 'output') selectedHost.append(el('p', `Exact amount: ${group.output[0]} · exact index: ${group.output[1]}`, 'tp-exact'));
                if (group.type === 'transaction') selectedHost.append(el('p', group.key, 'tp-exact'));
                selectedHost.append(el('p', group.type === 'related'
                    ? `The export reports ${group.counts.join(' / ')} shared candidate(s) for this pair. ${group.counts.length > 1 ? 'The directional reports disagree; no single reconciled count is assumed.' : 'Reciprocal reports are counted once.'} The shared output identities are not supplied by this relation field.`
                    : group.type === 'output' ? 'Each displayed ring contains this exact candidate output in its exported original membership. Membership does not identify the real spend.'
                        : 'Each displayed ring has an input context in this transaction. The inputs do not identify their source wallets.', 'tp-note'));
                const members = group.members.slice(0, 12);
                const network = el('div', null, 'tp-direct-network');
                network.append(el('div', group.type === 'transaction' ? 'Transaction input context' : group.type === 'output' ? 'Shared candidate output' : 'Reported overlap pair', 'tp-network-hub'));
                const nodes = el('div', null, 'tp-network-members');
                for (const ring of members) {
                    const row = rel.rings.get(ring), node = row && typeof hooks.onInspect === 'function' ? button(short(ring), () => hooks.onInspect(row)) : el('span', short(ring));
                    node.title = ring; node.className = 'tp-network-ring'; if (node.tagName === 'BUTTON') node.setAttribute('aria-label', `Inspect ring ${ring}`); nodes.append(node);
                }
                network.append(nodes); selectedHost.append(network);
                const details = append(el('details'), el('summary', `Exact identifiers and inspection (${members.length} of ${group.members.length} rings)`));
                table(details, 'Direct members of the selected relationship', ['Ring key image', 'Exported record', 'Evidence'], members.map(ring => {
                    const row = rel.rings.get(ring);
                    return [ring, row ? String(row.prediction_id ?? 'ID unknown') : 'Outside exported records', row && typeof hooks.onInspect === 'function' ? button('Inspect ring', () => hooks.onInspect(row)) : 'Details unavailable'];
                }));
                selectedHost.append(details);
            }
            renderGroups();
        }
        const api = {update(nextData) { if (!destroyed) { data = nextData; render(); } },
            destroy() { if (!destroyed) { destroyed = true; if (shell.parentNode === host) host.replaceChildren(); if (mounts.get(host) === api) mounts.delete(host); } },
            getReport() { return report; }};
        mounts.set(host, api); render(); return api;
    }
    return {mount, timeline, relationships, outputKey, observedTime};
}));
