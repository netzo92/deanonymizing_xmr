(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else { root.TraceGroveResearchActivity = api; api.boot(root); }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';
    const EVENTS = {first_recorded: 'First recorded', closed: 'Closed', reopened: 'Reopened', removed: 'Removed', reintroduced: 'Reintroduced'};
    const OUTCOMES = {supported: 'Supported within scope', refuted: 'Refuted within scope', mixed: 'Mixed evidence', inconclusive: 'Inconclusive', untested: 'Untested', not_applicable: 'Not a scientific verdict'};
    const EXECUTION = {not_started: 'Not started', in_progress: 'In progress', completed: 'Completed', deferred: 'Deferred'};
    const KINDS = {hypothesis: 'Hypothesis', measurement: 'Measurement', engineering: 'Engineering'};
    const REPO = 'https://github.com/netzo92/deanonymizing_xmr';
    const STORAGE_KEY = 'tracegrove.task-activity.seen.v1';
    const PAGE_SIZE = 8;
    const rows = value => Array.isArray(value) ? value : [];
    const sha = value => typeof value === 'string' && /^[a-f0-9]{40}$/i.test(value);
    const date = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
    const text = value => value == null ? '' : String(value);
    const prettyDate = value => date(value) ? `${new Date(value).toISOString().replace('T', ' ').slice(0, 19)} UTC` : 'Unknown';
    function sourceURL(path, commit) {
        if (typeof path !== 'string' || !/^[a-zA-Z0-9_./-]+$/.test(path) || path.startsWith('/') || path.split('/').includes('..')) return null;
        return `${REPO}/blob/${sha(commit) ? commit : 'main'}/${path}`;
    }
    function eventKey(event) { return `${event.commit}:${event.task_id}:${event.kind}`; }
    function activityData(data) {
        if (data?.schema_version !== 1 || !Array.isArray(data.tasks) || !Array.isArray(data.events) || typeof data.content_sha256 !== 'string') throw Error('Unsupported task activity export.');
        const taskIds = new Set();
        for (const task of data.tasks) {
            if (!task || typeof task.id !== 'string' || taskIds.has(task.id) || !sourceURL(task.note_id) || typeof task.title !== 'string' || !['open', 'completed', 'removed'].includes(task.status)) throw Error('Invalid or duplicate task identity.');
            taskIds.add(task.id);
            if (task.first_recorded && (!sha(task.first_recorded.commit) || !date(task.first_recorded.at) || !['open', 'completed'].includes(task.first_recorded.status))) throw Error('Invalid first-recorded task provenance.');
            if (!Array.isArray(task.events)) throw Error('Missing task event history.');
        }
        const eventIds = new Set();
        for (const event of data.events) {
            if (!event || !taskIds.has(event.task_id) || !Object.hasOwn(EVENTS, event.kind) || !sha(event.commit) || !date(event.at) || !sourceURL(event.note_id) || typeof event.title !== 'string' || !['open', 'completed', 'removed'].includes(event.status)) throw Error('Invalid committed task event.');
            const id = eventKey(event); if (eventIds.has(id)) throw Error('Duplicate task event.'); eventIds.add(id);
        }
        return data;
    }
    function ledgerData(data) {
        if (data?.schema_version !== 1 || !Array.isArray(data.entries)) throw Error('Unsupported hypothesis ledger.');
        const ids = new Set();
        for (const item of data.entries) {
            if (!item || typeof item.id !== 'string' || !item.id || ids.has(item.id) || !Object.hasOwn(KINDS, item.kind) || !Object.hasOwn(EXECUTION, item.execution_status) || !Object.hasOwn(OUTCOMES, item.outcome)) throw Error('Invalid or duplicate hypothesis entry.');
            ids.add(item.id);
            for (const key of ['question', 'theoretical_conclusion', 'measured_conclusion']) if (typeof item[key] !== 'string') throw Error('Incomplete hypothesis interpretation.');
            if (!Array.isArray(item.sources) || !Array.isArray(item.evidence) || !Array.isArray(item.task_refs) || !Array.isArray(item.remaining)) throw Error('Incomplete hypothesis provenance.');
            if (item.sources.some(source => !sourceURL(source.path) || typeof source.label !== 'string') || item.evidence.some(evidence => !sourceURL(evidence.path))) throw Error('Invalid hypothesis source.');
            if (item.kind === 'engineering' && item.outcome !== 'not_applicable') throw Error('Engineering completion is not a scientific verdict.');
        }
        return data;
    }
    function filterEvents(events, {kind = 'all', query = ''} = {}) {
        const q = text(query).trim().toLowerCase();
        return events.filter(event => (kind === 'all' || event.kind === kind) && (!q || [event.title, event.code, event.note_id, event.task_id].some(value => text(value).toLowerCase().includes(q))));
    }
    function filterHypotheses(entries, {outcome = 'all', execution = 'all', kind = 'all', query = ''} = {}) {
        const q = text(query).trim().toLowerCase();
        return entries.filter(item => (outcome === 'all' || item.outcome === outcome) && (execution === 'all' || item.execution_status === execution) && (kind === 'all' || item.kind === kind) && (!q || [item.id, item.question, item.theoretical_conclusion, item.measured_conclusion, ...item.task_refs.map(task => `${task.code || ''} ${task.title}`)].some(value => text(value).toLowerCase().includes(q))));
    }
    function unseenEvents(events, seen) {
        // A first visit establishes a baseline; the existing archive is not a new alert.
        return seen === null ? [] : events.filter(event => !seen.has(eventKey(event)));
    }
    function readSeen(storage) {
        try { const raw = storage.getItem(STORAGE_KEY); if (raw === null) return null; const value = JSON.parse(raw); return Array.isArray(value) && value.every(key => typeof key === 'string') ? new Set(value) : null; } catch { return null; }
    }
    function writeSeen(storage, events) {
        try { storage.setItem(STORAGE_KEY, JSON.stringify(events.map(eventKey))); return true; } catch { return false; }
    }
    function taskDates(task) {
        if (!task) return 'Task history unavailable.';
        const first = task.first_recorded;
        const closed = rows(task.events).filter(event => event.kind === 'closed').at(-1);
        const reopened = rows(task.events).filter(event => event.kind === 'reopened').at(-1);
        let value = first ? `First recorded ${prettyDate(first.at)}${first.status === 'completed' ? ' (already checked)' : ''}.` : 'Not yet recorded in a committed checklist.';
        value += closed ? ` Last recorded closure ${prettyDate(closed.at)}.` : ' Closure date not observed.';
        if (reopened) value += ` Last reopened ${prettyDate(reopened.at)}.`;
        if (task.pending_change) value += ' Current status includes an unpublished change; no event time assigned.';
        return value;
    }
    function preserveView(host, render) {
        const focused = document.activeElement;
        const key = host.contains(focused) ? focused.dataset.viewKey : null;
        const selection = focused && typeof focused.selectionStart === 'number' ? [focused.selectionStart, focused.selectionEnd] : null;
        const open = new Set([...host.querySelectorAll('details[open][data-view-key]')].map(node => node.dataset.viewKey));
        render();
        for (const node of host.querySelectorAll('details[data-view-key]')) node.open = open.has(node.dataset.viewKey);
        if (key) {
            const next = [...host.querySelectorAll('[data-view-key]')].find(node => node.dataset.viewKey === key);
            next?.focus({preventScroll: true});
            if (selection && next?.setSelectionRange) try { next.setSelectionRange(...selection); } catch {}
        }
    }
    const el = (tag, cls, value) => { const node = document.createElement(tag); if (cls) node.className = cls; if (value != null) node.textContent = text(value); return node; };
    const add = (host, ...nodes) => { host.append(...nodes.filter(Boolean)); return host; };
    const keyed = (node, key) => { node.dataset.viewKey = key; return node; };
    function link(label, url) { if (!url) return el('span', '', label); const node = el('a', '', label); node.href = url; if (url.startsWith('https://')) { node.target = '_blank'; node.rel = 'noopener noreferrer'; } return node; }
    function select(label, options, value, callback, key) {
        const input = keyed(el('select'), key); input.setAttribute('aria-label', label);
        for (const [key, title] of Object.entries(options)) { const option = el('option', '', title); option.value = key; input.append(option); }
        input.value = value; input.addEventListener('change', () => callback(input.value));
        return add(el('label'), el('span', '', label), input);
    }
    function search(label, value, callback, key) { const input = keyed(el('input'), key); input.type = 'search'; input.value = value; input.placeholder = 'Search question, task ID or source…'; input.setAttribute('aria-label', label); input.addEventListener('input', () => callback(input.value)); return add(el('label', 'research-search'), el('span', '', label), input); }
    function pages(host, count, page, callback, prefix) {
        const total = Math.max(1, Math.ceil(count / PAGE_SIZE));
        const previous = keyed(el('button', '', 'Previous'), `${prefix}-previous`); previous.type = 'button'; previous.disabled = page <= 0; previous.addEventListener('click', () => callback(page - 1));
        const next = keyed(el('button', '', 'Next'), `${prefix}-next`); next.type = 'button'; next.disabled = page >= total - 1; next.addEventListener('click', () => callback(page + 1));
        host.append(add(el('div', 'task-pagination'), el('span', '', `Page ${page + 1} of ${total} · ${count} matching entries`), add(el('div'), previous, next)));
    }
    function boot(root) {
        if (!root.document || !document.getElementById('task-activity')) return;
        const live = root.TraceGroveLive;
        if (!live) {
            for (const id of ['activity-feed-status', 'hypothesis-feed-status']) document.getElementById(id).textContent = 'The research refresh script is unavailable. Reload the page to retry.';
            document.getElementById('activity').setAttribute('aria-busy', 'false'); document.getElementById('hypotheses').setAttribute('aria-busy', 'false');
            return;
        }
        let activity = null, ledger = null, activityPage = 0, ledgerPage = 0, seen = null, initialized = false, persistent = true;
        let storage; try { storage = root.localStorage; } catch { persistent = false; }
        seen = readSeen(storage);
        const eventFilter = {kind: 'all', query: ''}, ledgerFilter = {outcome: 'all', execution: 'all', kind: 'all', query: ''};
        const activityHost = document.getElementById('task-activity'), ledgerHost = document.getElementById('hypothesis-ledger');
        function renderActivity() {
            const events = filterEvents([...activity.events].reverse(), eventFilter);
            activityPage = Math.max(0, Math.min(activityPage, Math.ceil(events.length / PAGE_SIZE) - 1));
            preserveView(activityHost, () => {
                const updates = unseenEvents(activity.events, seen);
                const badge = document.getElementById('task-update-badge'); badge.hidden = !updates.length; badge.textContent = `${updates.length} new`;
                const changes = el('p', 'activity-updates', updates.length ? `${updates.length} published task changes since you last marked activity as read.` : 'No unread published task changes.'); changes.setAttribute('role', 'status');
                const mark = keyed(el('button', '', 'Mark activity as read'), 'mark-read'); mark.type = 'button'; mark.disabled = !updates.length;
                mark.addEventListener('click', () => { seen = new Set(activity.events.map(eventKey)); persistent = writeSeen(storage, activity.events); renderActivity(); });
                const controls = add(el('div', 'research-controls'), search('Search task history', eventFilter.query, value => { eventFilter.query = value; activityPage = 0; renderActivity(); }, 'activity-search'), select('Change', {all: 'All changes', ...EVENTS}, eventFilter.kind, value => { eventFilter.kind = value; activityPage = 0; renderActivity(); }, 'activity-kind'));
                const list = el('ol', 'activity-list');
                for (const event of events.slice(activityPage * PAGE_SIZE, (activityPage + 1) * PAGE_SIZE)) {
                    const item = el('li', `activity-item activity-${event.kind}`);
                    const header = add(el('div', 'activity-item-meta'), el('span', 'activity-kind', EVENTS[event.kind]), el('time', '', prettyDate(event.at))); header.querySelector('time').dateTime = event.at;
                    const taskURL = `?status=all&id=${encodeURIComponent(event.task_id)}${event.code ? `&task=${encodeURIComponent(event.code)}` : ''}#tasks`;
                    add(item, header, add(el('h3'), keyed(link(event.title, taskURL), `event-${eventKey(event)}`)));
                    if (event.kind === 'first_recorded') item.append(el('p', '', event.status === 'completed' ? 'Already checked when first recorded. The actual creation and closure dates are unknown.' : 'First appearance in available committed history. Actual creation time is unknown.'));
                    if (event.kind === 'removed') item.append(el('p', '', 'Removed from the checklist. Removal does not establish completion or a research verdict.'));
                    add(item, add(el('div', 'activity-sources'), link(`Commit ${event.commit.slice(0, 8)} ↗`, `${REPO}/commit/${event.commit}`), link(event.kind === 'removed' ? 'First recorded source ↗' : 'Source at this revision ↗', sourceURL(event.note_id, event.kind === 'removed' ? activity.tasks.find(task => task.id === event.task_id)?.first_recorded?.commit : event.commit)), el('span', '', `${event.code || 'Uncoded task'} · ${event.status}`)));
                    list.append(item);
                }
                if (!events.length) list.append(el('li', 'task-empty', 'No committed changes match these filters.'));
                activityHost.replaceChildren(add(el('div', 'activity-read-row'), changes, mark), el('p', 'research-small', persistent ? 'Unread markers are saved only in this browser. First visits start from the current archive; mark as read after reviewing changes.' : 'Browser storage is unavailable. Unread markers work for this visit only.'), controls, list);
                if (activity.working_tree_changes) activityHost.append(el('p', 'research-caveat', `${activity.summary?.pending ?? 'Some'} checklist entries have unpublished changes. Dates below come only from committed history.`));
                pages(activityHost, events.length, activityPage, value => { activityPage = value; renderActivity(); }, 'activity');
                activityHost.append(el('p', 'research-small', `${activity.history?.mode === 'first_parent' ? 'First-parent Git history' : 'Available Git history'} · ${activity.history?.commit_count ?? 'Unknown'} commits · ${activity.history?.complete === true ? 'complete available history' : 'history coverage incomplete or unknown'} · dates use commit timestamps, not experiment execution times.`));
            });
        }
        function renderLedger() {
            const entries = filterHypotheses(ledger.entries, ledgerFilter);
            ledgerPage = Math.max(0, Math.min(ledgerPage, Math.ceil(entries.length / PAGE_SIZE) - 1));
            preserveView(ledgerHost, () => {
                const summary = el('div', 'ledger-totals');
                for (const [value, label] of [['supported', 'Scoped support'], ['refuted', 'Scoped refutation'], ['inconclusive', 'Inconclusive'], ['untested', 'Untested'], ['not_applicable', 'Engineering / no verdict']]) summary.append(add(el('div'), el('strong', '', ledger.entries.filter(item => item.outcome === value).length), el('span', '', label)));
                const controls = add(el('div', 'research-controls'), search('Search hypotheses and findings', ledgerFilter.query, value => { ledgerFilter.query = value; ledgerPage = 0; renderLedger(); }, 'ledger-search'), select('Verdict', {all: 'All verdicts', ...OUTCOMES}, ledgerFilter.outcome, value => { ledgerFilter.outcome = value; ledgerPage = 0; renderLedger(); }, 'ledger-outcome'), select('Execution', {all: 'All execution states', ...EXECUTION}, ledgerFilter.execution, value => { ledgerFilter.execution = value; ledgerPage = 0; renderLedger(); }, 'ledger-execution'), select('Entry type', {all: 'All types', ...KINDS}, ledgerFilter.kind, value => { ledgerFilter.kind = value; ledgerPage = 0; renderLedger(); }, 'ledger-kind'));
                const list = el('div', 'hypothesis-list');
                for (const entry of entries.slice(ledgerPage * PAGE_SIZE, (ledgerPage + 1) * PAGE_SIZE)) {
                    const card = el('article', 'hypothesis-card'); card.id = `hypothesis-${entry.id.replace(/[^a-zA-Z0-9-]/g, '-')}`;
                    add(card, add(el('div', 'hypothesis-marker'), el('span', '', `${entry.id} · ${KINDS[entry.kind]}`), el('span', 'execution-state', `Execution: ${EXECUTION[entry.execution_status]}`)), el('h3', '', entry.question), el('p', `hypothesis-verdict verdict-${entry.outcome}`, `Verdict: ${OUTCOMES[entry.outcome]}`));
                    add(card, el('h4', '', 'What the data showed'), el('p', '', entry.measured_conclusion), el('h4', '', 'Theoretical interpretation'), el('p', '', entry.theoretical_conclusion));
                    const detail = keyed(add(el('details'), keyed(el('summary', '', 'Evidence, scope and remaining work'), `summary-${entry.id}`)), `evidence-${entry.id}`);
                    for (const evidence of entry.evidence) {
                        const block = el('div', 'ledger-evidence');
                        add(block, el('p', '', evidence.scope || 'Scope not recorded.'), el('p', 'research-small', `Observation date: ${prettyDate(evidence.observed_at)}`));
                        const facts = el('dl');
                        for (const [label, value] of Object.entries(evidence.denominators || {})) add(facts, el('dt', '', label.replaceAll('_', ' ')), el('dd', '', typeof value === 'object' ? JSON.stringify(value) : value));
                        for (const [label, value] of [['Run ID', evidence.run_id], ['Source commit at execution', evidence.source_commit], ['Artifact SHA-256', evidence.sha256]]) if (value) add(facts, el('dt', '', label), el('dd', '', value));
                        add(block, facts, link('Inspect saved evidence ↗', sourceURL(evidence.path))); detail.append(block);
                    }
                    if (!entry.evidence.length) detail.append(el('p', 'research-caveat', 'No frozen measurement artifact is linked to this entry. A checklist status alone is not a measured result.'));
                    detail.append(el('h4', '', 'Remaining work'));
                    if (entry.remaining.length) { const remaining = el('ul'); for (const step of entry.remaining) remaining.append(el('li', '', step)); detail.append(remaining); }
                    else detail.append(el('p', '', 'No further steps recorded for this scoped question.'));
                    const sources = el('div', 'ledger-sources'); for (const source of entry.sources) sources.append(link(`${source.label} ↗`, sourceURL(source.path))); detail.append(sources);
                    const tasks = el('div', 'ledger-tasks'); for (const task of entry.task_refs) tasks.append(link(`${task.code || task.title} →`, task.id ? `?status=all&id=${encodeURIComponent(task.id)}${task.code ? `&task=${encodeURIComponent(task.code)}` : ''}#tasks` : task.code ? `?status=all&task=${encodeURIComponent(task.code)}#tasks` : `?status=all&q=${encodeURIComponent(task.title)}#tasks`));
                    add(card, detail, tasks); list.append(card);
                }
                if (!entries.length) list.append(el('p', 'task-empty', 'No research questions match these filters.'));
                ledgerHost.replaceChildren(el('p', 'research-small', `${ledger.entries.length} recorded entries · reviewed ${ledger.reviewed_at || 'date unknown'} · ${ledger.scope || 'Scope not recorded.'}`), summary, controls, list);
                pages(ledgerHost, entries.length, ledgerPage, value => { ledgerPage = value; renderLedger(); }, 'ledger');
            });
        }
        function status(id, result, hasData, label) {
            const host = document.getElementById(id);
            host.textContent = result.ok ? `${label} checked ${prettyDate(result.checkedAt)} · checks every 60 seconds for published changes.` : `Update check failed (${result.error}). ${hasData ? 'Keeping the last valid displayed snapshot.' : 'No valid snapshot is available here yet.'} Retrying every 60 seconds.`;
        }
        const activityPoller = live.startPolling({load: async () => activityData(await live.fetchJSON('task-activity.json')), onData(data) {
            if (!initialized && seen === null) { seen = new Set(data.events.map(eventKey)); persistent = writeSeen(storage, data.events); } initialized = true;
            activity = data; renderActivity(); root.TraceGroveTaskActivitySnapshot = data; root.dispatchEvent(new CustomEvent('tracegrove:task-activity', {detail: data}));
        }, onStatus(result) { document.getElementById('activity').setAttribute('aria-busy', 'false'); status('activity-feed-status', result, !!activity, 'Task history'); if (!activity && !result.ok) activityHost.replaceChildren(el('p', 'progress-loading', 'Task history has not been published or could not be validated. Current checklists remain available below.')); }});
        const ledgerPoller = live.startPolling({load: async () => ledgerData(await live.fetchJSON('hypotheses.json')), onData(data) { ledger = data; renderLedger(); }, onStatus(result) { document.getElementById('hypotheses').setAttribute('aria-busy', 'false'); status('hypothesis-feed-status', result, !!ledger, 'Hypothesis ledger'); if (!ledger && !result.ok) ledgerHost.replaceChildren(el('p', 'progress-loading', 'The hypothesis ledger has not been published or could not be validated. No verdicts are inferred.')); }});
        root.TraceGroveResearchPollers = {activity: activityPoller, ledger: ledgerPoller};
    }
    return {EVENTS, OUTCOMES, EXECUTION, KINDS, activityData, ledgerData, filterEvents, filterHypotheses, eventKey, unseenEvents, readSeen, writeSeen, taskDates, sourceURL, preserveView, prettyDate, boot};
});
