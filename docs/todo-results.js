(function (root) {
    'use strict';

    const array = value => Array.isArray(value) ? value : [];
    const text = value => value == null ? '' : String(value);
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const count = value => finite(value) ? value.toLocaleString() : 'Unknown';
    const percent = value => finite(value) ? `${(100 * value).toFixed(2)}%` : 'Unknown';
    const BRANCHES = { root: 'Overview', architecture: 'Architecture', research: 'Research', workflows: 'Workflows', tasks: 'Dashboard' };
    const PAGE_SIZE = 8;

    function plainMarkdown(value) {
        return text(value).replace(/\[([^\]]+)\]\([^\n]*?\)/g, '$1').replace(/\*\*([^*]+)\*\*/g, '$1').replace(/`([^`]+)`/g, '$1').trim();
    }

    function parseTask(todo, node, index) {
        const raw = text(todo.text);
        const heading = /^\s*\*\*(.+?)\*\*\s*(.*)$/s.exec(raw);
        const full = plainMarkdown(raw);
        const title = heading ? plainMarkdown(heading[1]) : full.length > 120 ? `${full.slice(0, 117)}…` : full;
        return {
            key: `${node.id}:${index}`, code: /^([A-Z]{1,4}\d+)\s*[—:-]/.exec(title)?.[1] || null,
            title, body: heading ? plainMarkdown(heading[2]) : full.length > 120 ? full : '',
            text: full, done: todo.done, branch: node.branch || 'root',
            noteId: node.id, noteTitle: node.title || node.id, reviewed: node.reviewed || 'unknown',
        };
    }

    function flattenTasks(brain) {
        return array(brain.nodes).filter(node => node && typeof node.id === 'string').flatMap(node => array(node.todos).filter(todo => todo && typeof todo.text === 'string' && typeof todo.done === 'boolean').map((todo, index) => parseTask(todo, node, index)));
    }

    function filterTasks(tasks, filters = {}) {
        const query = text(filters.query).trim().toLowerCase();
        return tasks.filter(task => {
            if (filters.status === 'open' && task.done) return false;
            if (filters.status === 'done' && !task.done) return false;
            if (filters.branch && filters.branch !== 'all' && task.branch !== filters.branch) return false;
            if (filters.task && task.code !== filters.task) return false;
            return !query || [task.text, task.noteTitle, task.noteId, task.code].some(value => text(value).toLowerCase().includes(query));
        });
    }

    function readFilters(search) {
        const params = new URLSearchParams(search);
        return {
            status: ['open', 'done', 'all'].includes(params.get('status')) ? params.get('status') : 'open',
            branch: Object.hasOwn(BRANCHES, params.get('branch')) ? params.get('branch') : 'all',
            query: params.get('q') || '',
            task: /^[A-Z]{1,4}\d+$/.test(params.get('task') || '') ? params.get('task') : '',
        };
    }

    function sourceURL(path, revision = 'main') {
        if (typeof path !== 'string' || !/^[a-zA-Z0-9_./-]+$/.test(path) || path.startsWith('/') || path.split('/').some(part => part === '..')) return null;
        const version = /^[a-f\d]{40}$/i.test(revision) ? revision : 'main';
        return `https://github.com/netzo92/deanonymizing_xmr/blob/${version}/${path}`;
    }

    function baselineRows(comparison) {
        if (!Number.isSafeInteger(comparison?.denominator) || comparison.denominator < 1) return [];
        return array(comparison.rows).filter(row => row && row.rings === comparison.denominator && finite(row.expected_agreement) && row.expected_agreement >= 0 && row.expected_agreement <= 1 && finite(row.expected_correct) && row.expected_correct >= 0 && row.expected_correct <= row.rings && Math.abs(row.expected_correct / row.rings - row.expected_agreement) < 1e-10);
    }

    const el = (tag, className, value) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (value != null) node.textContent = text(value);
        return node;
    };
    const add = (parent, ...children) => { parent.append(...children.filter(child => child != null)); return parent; };
    const byId = id => document.getElementById(id);
    function link(label, href) {
        if (!href) return el('span', '', label);
        const anchor = el('a', '', label); anchor.href = href;
        if (href.startsWith('https://')) { anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; }
        return anchor;
    }
    function errorState(host, title, error, retry) {
        const box = add(el('div', 'progress-error'), el('h3', '', title), el('p', '', error.message || String(error)));
        box.setAttribute('role', 'alert');
        const button = el('button', '', 'Retry'); button.type = 'button'; button.addEventListener('click', retry);
        host.replaceChildren(add(box, button));
    }

    async function boot() {
        let brain = null;
        let progress = null;
        let tasks = [];
        let state = readFilters(location.search);
        let page = 0;

        function syncURL() {
            const url = new URL(location.href);
            for (const key of ['status', 'branch', 'q', 'task']) url.searchParams.delete(key);
            if (state.status !== 'open') url.searchParams.set('status', state.status);
            if (state.branch !== 'all') url.searchParams.set('branch', state.branch);
            if (state.query) url.searchParams.set('q', state.query);
            if (state.task) url.searchParams.set('task', state.task);
            history.replaceState(null, '', url);
        }

        function renderTasks() {
            const host = byId('task-browser');
            const controls = el('div', 'task-controls');
            const searchLabel = add(el('label', 'task-search'), el('span', '', 'Search tasks and ideas'));
            const search = el('input'); search.type = 'search'; search.placeholder = 'Feature ablation, grouping, FA2…'; search.value = state.query || state.task; search.setAttribute('aria-label', 'Search research tasks'); searchLabel.append(search);
            const branchLabel = add(el('label', 'task-branch'), el('span', '', 'Area'));
            const branch = el('select'); branch.setAttribute('aria-label', 'Task area');
            for (const [value, label] of [['all', 'All areas'], ...Object.entries(BRANCHES)]) { const option = el('option', '', label); option.value = value; branch.append(option); }
            branch.value = state.branch; branchLabel.append(branch);
            const states = el('div', 'task-states'); states.setAttribute('aria-label', 'Checklist completion status');
            const statusButtons = new Map();
            for (const [value, label] of [['open', 'Open'], ['done', 'Completed'], ['all', 'All']]) {
                const button = el('button', '', label); button.type = 'button'; button.addEventListener('click', () => { state.status = value; page = 0; refresh(); });
                states.append(button); statusButtons.set(value, button);
            }
            const reset = el('button', 'task-reset', 'Reset'); reset.type = 'button'; reset.addEventListener('click', () => { state = { status: 'open', branch: 'all', query: '', task: '' }; page = 0; search.value = ''; branch.value = 'all'; refresh(); });
            add(controls, searchLabel, branchLabel, states, reset);
            const statusRow = el('div', 'task-status-row'); const status = el('p'); status.setAttribute('role', 'status');
            const completed = tasks.filter(task => task.done).length;
            const completion = el('p', 'task-completion', `${completed} / ${tasks.length} checklist items complete`);
            const bar = el('span', 'task-completion-bar'); bar.setAttribute('aria-hidden', 'true'); const fill = el('span'); fill.style.width = `${tasks.length ? 100 * completed / tasks.length : 0}%`; completion.append(add(bar, fill));
            add(statusRow, status, completion);
            const list = el('div', 'task-list');
            const pagination = el('div', 'task-pagination'); const pageLabel = el('span'); const buttons = el('div');
            const previous = el('button', '', 'Previous'); previous.type = 'button';
            const next = el('button', '', 'Next'); next.type = 'button';
            previous.addEventListener('click', () => { page--; refresh(); }); next.addEventListener('click', () => { page++; refresh(); });
            add(pagination, pageLabel, add(buttons, previous, next));
            host.replaceChildren(controls, statusRow, list, pagination);

            function refresh() {
                const filtered = filterTasks(tasks, state);
                const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE)); page = Math.max(0, Math.min(page, pages - 1));
                for (const [value, button] of statusButtons) button.setAttribute('aria-pressed', String(state.status === value));
                list.replaceChildren();
                for (const task of filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) {
                    const card = el('article', `todo-card ${task.done ? 'is-done' : ''}`);
                    const header = add(el('div', 'todo-card-header'), el('span', `todo-state ${task.done ? 'is-done' : ''}`, task.done ? '✓ Completed' : '○ Open'), el('span', 'todo-branch', BRANCHES[task.branch] || task.branch));
                    add(card, header, el('h3', '', task.title));
                    if (task.body.length > 270) {
                        card.append(el('p', '', `${task.body.slice(0, 267)}…`));
                        const detail = add(el('details'), el('summary', '', 'Details and completion criteria'), el('p', '', task.body)); card.append(detail);
                    } else if (task.body) card.append(el('p', '', task.body));
                    card.append(add(el('div', 'todo-source'), link(task.noteTitle + ' ↗', sourceURL(task.noteId, brain.source_commit)), el('span', '', `Reviewed ${task.reviewed}`)));
                    list.append(card);
                }
                if (!filtered.length) list.append(el('p', 'task-empty', 'No checklist items match these filters. Try another area or completion status.'));
                status.textContent = `${filtered.length} matching ${state.status === 'all' ? '' : state.status === 'done' ? 'completed ' : 'open '}items · from ${tasks.length} exported checklist entries`;
                pageLabel.textContent = `Page ${page + 1} of ${pages} · ${PAGE_SIZE} items per page`;
                previous.disabled = page === 0; next.disabled = page >= pages - 1;
                syncURL();
            }
            search.addEventListener('input', () => { state.query = search.value; state.task = ''; page = 0; refresh(); });
            branch.addEventListener('change', () => { state.branch = branch.value; page = 0; refresh(); });
            refresh();
        }

        function renderResults() {
            const host = byId('measured-results'); const grid = el('div', 'result-grid');
            const experiments = progress.experiments.filter(item => item.status === 'completed' && item.evidence_kind === 'measured');
            byId('experiment-total').textContent = experiments.length;
            const scope = progress.scope || {};
            byId('snapshot-range').textContent = `Heights ${count(scope.scan_start)}–${count(scope.scan_end)}`;
            byId('snapshot-description').textContent = scope.description || 'Scope unavailable.';
            for (const experiment of experiments) {
                const card = el('article', 'result-card'); card.id = `result-${experiment.id.replace(/[^a-z\d-]/gi, '-')}`;
                add(card, add(el('div', 'result-marker'), el('span', '', '✓ Completed · measured'), el('span', '', experiment.id)), el('h3', '', experiment.title), add(el('div', 'result-metric'), el('strong', '', count(experiment.metric?.value)), el('span', '', experiment.metric?.label)), el('p', '', experiment.finding), el('p', 'result-limit', experiment.limitation), link('Read the experiment and method ↗', sourceURL(experiment.note_id, brain?.source_commit)));
                const details = add(el('details'), el('summary', '', 'Frozen provenance and source artifact'));
                const facts = el('dl');
                for (const [label, value] of [['Started (UTC)', experiment.started_at], ['Git HEAD at execution', experiment.source_revision], ['Executed script SHA-256', experiment.script_sha256], ['Source database SHA-256', experiment.source_main_sha256], ['WAL SHA-256', experiment.source_wal_sha256 || 'Not fingerprinted in this v1 audit'], ['Result artifact SHA-256', experiment.artifact_sha256]]) add(facts, el('dt', '', label), el('dd', '', value));
                add(details, facts, link('Open saved artifact ↗', sourceURL(experiment.artifact_path)));
                card.append(details); grid.append(card);
            }
            host.replaceChildren(grid);
            renderBaselines(host, progress.baseline_comparison);
            const conclusions = el('div', 'conclusion-grid');
            for (const conclusion of array(progress.theoretical_conclusions).filter(item => item.status === 'theoretical')) {
                conclusions.append(add(el('article', 'conclusion-card'), el('div', 'conclusion-kind', 'Theoretical implication · untested benefit'), el('h3', '', conclusion.title), el('p', '', conclusion.statement), link('Inspect the supporting context ↗', sourceURL(conclusion.note_id, brain?.source_commit))));
            }
            byId('theoretical-conclusions').replaceChildren(conclusions);
            byId('progress-provenance').textContent = `Summary snapshot ${text(progress.generated_at)} · experiments retain their original dates and source hashes.`;
            renderNext();
        }

        function renderBaselines(host, comparison) {
            const rows = baselineRows(comparison);
            const panel = el('section', 'baseline-panel');
            add(panel, el('h3', '', 'Four baselines, one selected historical population'), el('p', '', `Expected agreement with current stored deterministic labels · denominator: ${count(comparison?.denominator)} eligible multi-member rings · ${count(comparison?.candidate_rows)} original candidate rows.`));
            if (rows.length !== array(comparison?.rows).length || !rows.length) {
                panel.append(el('p', 'interpretation-note', 'The baseline rows are missing or do not reconcile with their denominator. No comparison is plotted.')); host.append(panel); return;
            }
            const bars = el('div', 'baseline-bars'); bars.setAttribute('role', 'img'); bars.setAttribute('aria-label', 'Baseline expected agreement. Exact values and tie counts are in the table below.');
            for (const row of rows) { const track = el('div', 'baseline-track'); const fill = el('div', 'baseline-fill'); fill.style.width = `${row.expected_agreement * 100}%`; bars.append(add(el('div', 'baseline-row'), el('span', '', row.label), add(track, fill), el('strong', '', percent(row.expected_agreement)))); }
            panel.append(bars);
            const wrapper = el('div', 'baseline-table'); wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', 'Scrollable baseline comparison table');
            const table = el('table'); const header = el('tr');
            for (const label of ['Rule', 'Expected agreement', 'Expected selections agreeing / N', 'Tied-choice rings', 'Unique-choice fraction']) { const th = el('th', '', label); th.scope = 'col'; header.append(th); }
            table.append(add(el('thead'), header)); const body = el('tbody');
            for (const row of rows) {
                const tr = el('tr');
                for (const value of [row.label, percent(row.expected_agreement), `${row.expected_correct.toLocaleString(undefined, { maximumFractionDigits: 6 })} / ${count(row.rings)}`, count(row.tie_rings), percent(row.unique_top_choice_fraction)]) tr.append(el('td', '', value));
                body.append(tr);
            }
            panel.append(add(wrapper, add(table, body)), el('p', '', comparison.tie_method), el('p', 'interpretation-note', comparison.interpretation), link('Exact rational totals, cohorts, and artifact ↗', sourceURL(comparison.artifact_path)));
            host.append(panel);
        }

        function renderNext() {
            if (!progress) return;
            const list = el('div', 'next-list');
            for (const suggestion of array(progress.next_priorities)) {
                const task = tasks.find(item => item.code === suggestion.task_id);
                const status = !brain ? 'Checklist status unavailable' : !task ? 'Proposed · no matching checklist entry' : task.done ? 'Completed in current checklist' : 'Open · proposed experiment';
                const content = add(el('div'), el('span', `todo-state ${task?.done ? 'is-done' : ''}`, status), el('h3', '', `${suggestion.task_id} · ${suggestion.title}`), el('p', '', suggestion.why));
                const href = `?status=all&task=${encodeURIComponent(suggestion.task_id)}#tasks`;
                list.append(add(el('article', 'next-card'), content, link('View checklist →', href)));
            }
            byId('next-experiments').replaceChildren(list);
        }

        async function loadBrain() {
            byId('tasks').setAttribute('aria-busy', 'true');
            try {
                const response = await fetch('brain.json'); if (!response.ok) throw Error(`Checklist export unavailable (HTTP ${response.status}).`);
                const data = await response.json(); if (data?.schema_version !== 1 || !Array.isArray(data.nodes)) throw Error('The checklist export is missing or unsupported.');
                brain = data; tasks = flattenTasks(brain); const done = tasks.filter(item => item.done).length;
                byId('open-total').textContent = tasks.length - done; byId('done-total').textContent = done;
                renderTasks(); renderNext();
            } catch (error) { errorState(byId('task-browser'), 'Checklists could not be loaded', error, loadBrain); }
            finally { byId('tasks').setAttribute('aria-busy', 'false'); }
        }

        async function loadProgress() {
            byId('results').setAttribute('aria-busy', 'true');
            try {
                const response = await fetch('research-progress.json'); if (!response.ok) throw Error(`Research snapshot unavailable (HTTP ${response.status}).`);
                const data = await response.json(); if (data?.schema_version !== 1 || !Array.isArray(data.experiments)) throw Error('The research snapshot is missing or unsupported.');
                progress = data; renderResults();
            } catch (error) {
                errorState(byId('measured-results'), 'Measured results could not be loaded', error, loadProgress);
                byId('theoretical-conclusions').replaceChildren(el('p', 'progress-loading', 'The theoretical summary is unavailable until its source snapshot loads.'));
                byId('next-experiments').replaceChildren(el('p', 'progress-loading', 'Suggested experiments are unavailable. The canonical checklists above can still be used.'));
            } finally { byId('results').setAttribute('aria-busy', 'false'); }
        }

        root.addEventListener('popstate', () => { state = readFilters(location.search); page = 0; if (brain) renderTasks(); });
        await Promise.allSettled([loadBrain(), loadProgress()]);
        const anchor = document.getElementById(location.hash.slice(1)); if (anchor) anchor.scrollIntoView({ block: 'start' });
    }

    if (typeof module !== 'undefined' && module.exports) module.exports = { plainMarkdown, parseTask, flattenTasks, filterTasks, readFilters, sourceURL, baselineRows };
    if (root && typeof document !== 'undefined') boot();
})(typeof window !== 'undefined' ? window : null);
