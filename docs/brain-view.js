(function (global) {
    'use strict';

    const BRANCHES = {
        root: { label: 'Overview', color: '#ffad7a' },
        architecture: { label: 'Architecture', color: '#79b8ff' },
        research: { label: 'Research', color: '#c8a4ff' },
        workflows: { label: 'Workflows', color: '#6dd5bb' },
        tasks: { label: 'TODOs', color: '#e7c467' },
    };
    const array = value => Array.isArray(value) ? value : [];
    const text = value => value === null || value === undefined ? '' : String(value);
    const branchInfo = branch => BRANCHES[branch] || { label: 'Other', color: '#a1aebd' };
    const isDone = todo => todo.done === true;
    let instance = 0;

    function resolveLink(href, currentId, knownIds, sourceCommit) {
        const raw = text(href).trim();
        if (!raw || /[\u0000-\u001f\u007f]/.test(raw) || raw.startsWith('//') || raw.includes('\\')) return null;
        if (/^[a-z][a-z\d+.-]*:/i.test(raw)) {
            try {
                const url = new URL(raw);
                return ['https:', 'http:'].includes(url.protocol) ? { kind: 'external', url: url.href } : null;
            } catch { return null; }
        }
        const hashIndex = raw.indexOf('#');
        const fragment = hashIndex >= 0 ? raw.slice(hashIndex + 1) : '';
        const beforeHash = hashIndex >= 0 ? raw.slice(0, hashIndex) : raw;
        const queryIndex = beforeHash.indexOf('?');
        const path = queryIndex >= 0 ? beforeHash.slice(0, queryIndex) : beforeHash;
        const query = queryIndex >= 0 ? beforeHash.slice(queryIndex) : '';
        let decoded;
        try { decoded = decodeURIComponent(path); } catch { return null; }
        if (decoded.includes('\\') || /[\u0000-\u001f\u007f]/.test(decoded)) return null;
        const segments = decoded.startsWith('/') ? [] : text(currentId).split('/').slice(0, -1);
        if (!decoded) segments.push(text(currentId).split('/').at(-1));
        else for (const part of decoded.split('/')) {
            if (!part || part === '.') continue;
            if (part === '..') { if (!segments.length) return null; segments.pop(); }
            else segments.push(part);
        }
        const id = segments.join('/');
        if (knownIds.has(id)) return { kind: 'note', id, fragment };
        const revision = /^[a-f\d]{7,40}$/i.test(text(sourceCommit)) ? sourceCommit : 'main';
        return { kind: 'external', url: `https://github.com/netzo92/deanonymizing_xmr/blob/${revision}/${segments.map(encodeURIComponent).join('/')}${query}${fragment ? `#${fragment}` : ''}` };
    }

    function tokenizeInline(value) {
        const source = text(value);
        const tokens = [];
        let plain = '';
        const flush = () => { if (plain) { tokens.push({ type: 'text', text: plain }); plain = ''; } };
        // Balanced link destinations allow URLs containing parentheses.
        const findBalanced = (start, open, close) => {
            let depth = 1;
            for (let i = start + 1; i < source.length; i++) {
                if (source[i] === '\\') { i++; continue; }
                if (source[i] === open) depth++;
                else if (source[i] === close && --depth === 0) return i;
            }
            return -1;
        };
        for (let i = 0; i < source.length;) {
            if (source[i] === '\\' && i + 1 < source.length) { plain += source[i + 1]; i += 2; continue; }
            if (source[i] === '`') {
                let length = 1;
                while (source[i + length] === '`') length++;
                const delimiter = '`'.repeat(length);
                const end = source.indexOf(delimiter, i + length);
                if (end >= 0) { flush(); tokens.push({ type: 'code', text: source.slice(i + length, end) }); i = end + length; continue; }
            }
            if (source[i] === '[') {
                const labelEnd = findBalanced(i, '[', ']');
                if (labelEnd >= 0 && source[labelEnd + 1] === '(') {
                    const end = findBalanced(labelEnd + 1, '(', ')');
                    if (end >= 0) {
                        flush(); tokens.push({ type: 'link', text: source.slice(i + 1, labelEnd), href: source.slice(labelEnd + 2, end).trim() });
                        i = end + 1; continue;
                    }
                }
            }
            const delimiter = source.startsWith('**', i) ? '**' : source[i] === '*' ? '*' : null;
            if (delimiter) {
                const end = source.indexOf(delimiter, i + delimiter.length);
                if (end > i + delimiter.length) {
                    flush(); tokens.push({ type: delimiter === '**' ? 'strong' : 'em', text: source.slice(i + delimiter.length, end) });
                    i = end + delimiter.length; continue;
                }
            }
            plain += source[i++];
        }
        flush();
        return tokens;
    }

    function markdownBlocks(markdown) {
        const lines = text(markdown).replace(/\r\n?/g, '\n').split('\n');
        const blocks = [];
        const isFence = line => /^\s*(`{3,}|~{3,})/.exec(line);
        const listItem = line => /^\s*(?:([-+*])|(\d+)[.)])\s+(?:\[([ xX])\]\s+)?(.*)$/.exec(line);
        const cells = line => line.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, '|'));
        const separator = line => /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
        for (let i = 0; i < lines.length;) {
            const line = lines[i];
            if (!line.trim()) { i++; continue; }
            const fence = isFence(line);
            if (fence) {
                const code = [];
                const delimiter = fence[1];
                i++;
                while (i < lines.length && !lines[i].trim().startsWith(delimiter)) code.push(lines[i++]);
                if (i < lines.length) i++;
                blocks.push({ type: 'code', text: code.join('\n') }); continue;
            }
            const heading = /^(#{1,6})\s+(.+)$/.exec(line);
            if (heading) { blocks.push({ type: 'heading', level: heading[1].length, text: heading[2].replace(/\s+#+$/, '') }); i++; continue; }
            if (i + 1 < lines.length && line.includes('|') && separator(lines[i + 1])) {
                const rows = []; const headers = cells(line); i += 2;
                while (i < lines.length && lines[i].trim() && lines[i].includes('|')) rows.push(cells(lines[i++]));
                blocks.push({ type: 'table', headers, rows }); continue;
            }
            const item = listItem(line);
            if (item) {
                const ordered = Boolean(item[2]); const checklist = item[3] !== undefined; const items = [];
                while (i < lines.length) {
                    const next = listItem(lines[i]);
                    if (!next || Boolean(next[2]) !== ordered || (next[3] !== undefined) !== checklist) break;
                    let itemText = next[4]; i++;
                    while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !listItem(lines[i]) && !isFence(lines[i])) itemText += ` ${lines[i++].trim()}`;
                    items.push({ text: itemText, done: next[3]?.toLowerCase() === 'x' });
                }
                blocks.push({ type: 'list', ordered, checklist, items }); continue;
            }
            if (/^>\s?/.test(line)) {
                const quoted = [];
                while (i < lines.length && /^>\s?/.test(lines[i])) quoted.push(lines[i++].replace(/^>\s?/, ''));
                blocks.push({ type: 'quote', text: quoted.join('\n') }); continue;
            }
            if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) { blocks.push({ type: 'rule' }); i++; continue; }
            const paragraph = [line]; i++;
            while (i < lines.length && lines[i].trim() && !isFence(lines[i]) && !/^#{1,6}\s|^>\s?/.test(lines[i]) && !listItem(lines[i]) && !(i + 1 < lines.length && separator(lines[i + 1]))) paragraph.push(lines[i++]);
            blocks.push({ type: 'paragraph', text: paragraph.join('\n') });
        }
        return blocks;
    }

    function filterNotes(nodes, filters = {}) {
        const query = text(filters.query).trim().toLowerCase();
        return nodes.filter(node => (!filters.branch || filters.branch === 'all' || node.branch === filters.branch) && (!query || [node.title, node.summary, node.content, node.path, node.id, ...array(node.todos).map(todo => todo.text)].some(value => text(value).toLowerCase().includes(query))));
    }

    function filterTodos(nodes, filters = {}) {
        const query = text(filters.query).trim().toLowerCase();
        return nodes.filter(node => !filters.branch || filters.branch === 'all' || node.branch === filters.branch).flatMap(node => array(node.todos).map((todo, index) => ({ node, todo, index }))).filter(({ node, todo }) => {
            if (filters.status === 'open' && isDone(todo)) return false;
            if (filters.status === 'done' && !isDone(todo)) return false;
            return !query || [todo.text, node.title, node.summary, node.path].some(value => text(value).toLowerCase().includes(query));
        });
    }

    function layoutGraph(nodes, rootId) {
        const layout = new Map();
        const root = nodes.find(node => node.id === rootId) || nodes[0];
        if (!root) return layout;
        layout.set(root.id, { x: 420, y: 315, root: true });
        const groups = [...new Set(nodes.filter(node => node !== root).map(node => node.branch))].map(branch => ({ branch, nodes: nodes.filter(node => node !== root && node.branch === branch) }));
        const totalWeight = groups.reduce((sum, group) => sum + Math.max(2, group.nodes.length), 0);
        let start = -Math.PI / 2;
        for (const group of groups) {
            const span = Math.max(2, group.nodes.length) / totalWeight * Math.PI * 2;
            const hub = group.nodes.find(node => /\/index\.md$/.test(node.id)) || group.nodes[0];
            const angle = start + span / 2;
            layout.set(hub.id, { x: 420 + 143 * Math.cos(angle), y: 315 + 124 * Math.sin(angle), hub: true });
            const leaves = group.nodes.filter(node => node !== hub);
            leaves.forEach((node, index) => {
                const leafAngle = start + span * (index + 1) / (leaves.length + 1);
                layout.set(node.id, { x: 420 + 307 * Math.cos(leafAngle), y: 315 + 250 * Math.sin(leafAngle) });
            });
            start += span;
        }
        // Labels are rectangles, so separate them after the radial branch layout.
        // Keep the root fixed and constrain every card to the visible canvas.
        const points = [...layout.values()];
        for (let pass = 0; pass < 90; pass++) {
            let overlaps = false;
            for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
                const a = points[i]; const b = points[j];
                const dx = b.x - a.x; const dy = b.y - a.y;
                const overlapX = 163 - Math.abs(dx); const overlapY = 69 - Math.abs(dy);
                if (overlapX <= 0 || overlapY <= 0) continue;
                overlaps = true;
                const axis = overlapX < overlapY ? 'x' : 'y';
                const shift = (axis === 'x' ? overlapX : overlapY) + .05;
                const direction = (axis === 'x' ? dx : dy) >= 0 ? 1 : -1;
                if (a.root) b[axis] += shift * direction;
                else if (b.root) a[axis] -= shift * direction;
                else { a[axis] -= shift / 2 * direction; b[axis] += shift / 2 * direction; }
            }
            for (const point of points) { point.x = Math.max(80, Math.min(760, point.x)); point.y = Math.max(34, Math.min(596, point.y)); }
            if (!overlaps) break;
        }
        return layout;
    }

    const el = (tag, className, content) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (content !== undefined) node.textContent = text(content);
        return node;
    };
    const add = (parent, ...children) => { for (const child of children) if (child !== null && child !== undefined) parent.append(child); return parent; };
    const svgEl = (tag, attributes = {}, content) => {
        const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
        for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
        if (content !== undefined) node.textContent = text(content);
        return node;
    };
    const slug = value => text(value).toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, '').trim().replace(/\s/g, '-');

    function renderInline(parent, value, context, depth = 0) {
        if (depth > 8) { parent.append(document.createTextNode(text(value))); return; }
        for (const token of tokenizeInline(value)) {
            if (token.type === 'text') parent.append(document.createTextNode(token.text));
            else if (token.type === 'code') parent.append(el('code', '', token.text));
            else if (token.type === 'strong' || token.type === 'em') {
                const node = el(token.type); renderInline(node, token.text, context, depth + 1); parent.append(node);
            } else {
                const target = resolveLink(token.href, context.note.id, context.ids, context.commit);
                if (!target) { parent.append(document.createTextNode(token.text)); continue; }
                const link = el('a');
                renderInline(link, token.text, context, depth + 1);
                if (target.kind === 'note') {
                    link.href = `#brain-note-${encodeURIComponent(target.id)}`;
                    link.addEventListener('click', event => { event.preventDefault(); context.select(target.id, true, target.fragment); });
                } else { link.href = target.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
                parent.append(link);
            }
        }
    }

    function renderMarkdown(parent, markdown, context) {
        const usedIds = new Map();
        for (const block of markdownBlocks(markdown)) {
            if (block.type === 'heading') {
                const heading = el(`h${Math.min(block.level + 1, 6)}`);
                const base = slug(block.text); const occurrence = usedIds.get(base) || 0; usedIds.set(base, occurrence + 1);
                heading.id = `${context.prefix}-${base}${occurrence ? `-${occurrence}` : ''}`;
                renderInline(heading, block.text, context); parent.append(heading);
            } else if (block.type === 'code') parent.append(add(el('pre'), el('code', '', block.text)));
            else if (block.type === 'rule') parent.append(el('hr'));
            else if (block.type === 'list') {
                const list = el(block.ordered ? 'ol' : 'ul', block.checklist ? 'brain-checklist' : '');
                for (const item of block.items) {
                    const li = el('li');
                    if (block.checklist) li.append(el('span', `brain-check-label ${item.done ? 'brain-done' : ''}`, item.done ? '✓ Completed' : '○ Open'));
                    renderInline(li, item.text, context); list.append(li);
                }
                parent.append(list);
            } else if (block.type === 'table') {
                const wrapper = el('div', 'brain-table-wrap'); wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', 'Scrollable note table');
                const table = el('table'); const tr = el('tr');
                for (const cell of block.headers) { const th = el('th'); th.scope = 'col'; renderInline(th, cell, context); tr.append(th); }
                table.append(add(el('thead'), tr));
                const body = el('tbody');
                for (const row of block.rows) { const cells = el('tr'); for (const content of row) { const td = el('td'); renderInline(td, content, context); cells.append(td); } body.append(cells); }
                parent.append(add(wrapper, add(table, body)));
            } else { const node = el(block.type === 'quote' ? 'blockquote' : 'p'); renderInline(node, block.text, context); parent.append(node); }
        }
    }

    function render(host, data) {
        if (data.schema_version !== 1 || !Array.isArray(data.nodes) || !Array.isArray(data.edges)) throw new Error('Unsupported or incomplete knowledge export.');
        const nodes = data.nodes.filter(node => node && typeof node.id === 'string');
        const ids = new Set(nodes.map(node => node.id));
        const byId = new Map(nodes.map(node => [node.id, node]));
        const edges = data.edges.filter(edge => edge && ids.has(edge.source) && ids.has(edge.target));
        const prefix = `tg-brain-${++instance}`;
        const rootId = ids.has(data.root_id) ? data.root_id : nodes[0]?.id;
        const state = { mode: global?.matchMedia?.('(max-width: 600px)').matches ? 'notes' : 'map', branch: 'all', query: '', status: 'open', selected: rootId, references: false };
        const todos = nodes.flatMap(node => array(node.todos));
        const complete = todos.filter(isDone).length;
        const shell = el('section', 'brain-shell');
        const header = el('div', 'brain-header');
        const intro = add(el('div'), el('div', 'brain-kicker', 'Inside the research'), el('h2', '', 'Explore the knowledge grove'), el('p', '', 'Follow the Markdown hierarchy from architecture to open questions. Select a note to read its context, evidence, and next steps.'));
        const metrics = el('div', 'brain-metrics');
        for (const [label, number, mode, status] of [['Notes', nodes.length, 'notes', 'all'], ['Open TODOs', todos.length - complete, 'todos', 'open'], ['Completed', complete, 'todos', 'done']]) {
            const button = add(el('button', 'brain-metric'), el('strong', '', number), el('span', '', label));
            button.type = 'button'; button.setAttribute('aria-label', `View ${number} ${label.toLowerCase()}`);
            button.addEventListener('click', () => { state.mode = mode; state.status = status; state.branch = 'all'; state.query = ''; search.value = ''; refresh(); });
            metrics.append(button);
        }
        shell.append(add(header, intro, metrics));
        const tools = el('div', 'brain-tools');
        const searchLabel = el('label', 'brain-search');
        const search = el('input'); search.type = 'search'; search.placeholder = 'Search notes, questions, or TODOs…'; search.setAttribute('aria-label', 'Search knowledge notes and TODOs');
        add(searchLabel, el('span', '', '⌕'), search);
        const reset = el('button', '', 'Reset'); reset.type = 'button'; reset.setAttribute('aria-label', 'Reset knowledge filters');
        add(tools, searchLabel, reset);
        const branches = el('div', 'brain-branches'); branches.setAttribute('aria-label', 'Filter knowledge branches');
        const branchButtons = new Map();
        for (const branch of ['all', ...Object.keys(BRANCHES).filter(key => nodes.some(node => node.branch === key))]) {
            const info = branch === 'all' ? { label: 'All branches', color: '#a1aebd' } : branchInfo(branch);
            const button = el('button', 'brain-pill'); button.type = 'button'; button.style.setProperty('--branch-color', info.color);
            if (branch !== 'all') button.append(el('span', 'brain-dot'));
            button.append(document.createTextNode(info.label));
            button.addEventListener('click', () => { state.branch = branch; refresh(); });
            branchButtons.set(branch, button); branches.append(button);
        }
        shell.append(add(tools, branches));
        const body = el('div', 'brain-body'); const explorer = el('div', 'brain-explorer');
        const tabbar = el('div', 'brain-tabbar'); tabbar.setAttribute('aria-label', 'Knowledge view');
        const tabs = new Map();
        for (const [mode, label] of [['map', 'Map'], ['notes', 'Notes'], ['todos', 'TODOs']]) {
            const button = el('button', 'brain-tab', label); button.type = 'button'; button.addEventListener('click', () => { state.mode = mode; refresh(); });
            tabbar.append(button); tabs.set(mode, button);
        }
        const matchCount = el('span', 'brain-match-count'); matchCount.setAttribute('role', 'status'); tabbar.append(matchCount);
        const canvasHost = el('div'); explorer.append(tabbar, canvasHost);
        const reader = el('article', 'brain-reader'); reader.tabIndex = -1; reader.setAttribute('aria-label', 'Selected knowledge note');
        shell.append(add(body, explorer, reader));
        const footer = el('div', 'brain-footer');
        footer.append(el('span', '', 'Links describe documentation structure. Open TODOs and research hypotheses are proposed work.'));
        footer.append(el('span', '', `Exported ${text(data.generated_at) || 'unknown'} · source ${text(data.source_commit).slice(0, 10) || 'unknown'}`));
        shell.append(footer); host.replaceChildren(shell);

        function context(note) { return { note, ids, commit: data.source_commit, prefix, select }; }
        function select(id, focus = false, fragment = '') {
            if (!byId.has(id)) return;
            state.selected = id; refresh();
            if (focus) { reader.focus({ preventScroll: true }); reader.scrollIntoView({ block: 'nearest' }); }
            if (fragment) {
                let decoded; try { decoded = decodeURIComponent(fragment); } catch { decoded = fragment; }
                const target = document.getElementById(`${prefix}-${decoded}`);
                if (target && reader.contains(target)) target.scrollIntoView({ block: 'nearest' });
            }
        }
        function renderReader(matches) {
            const previousScroll = reader.dataset.noteId === state.selected ? reader.scrollTop : 0;
            reader.replaceChildren();
            const note = byId.get(state.selected);
            if (!note) { reader.append(el('p', 'brain-empty', 'No notes are included in this export.')); return; }
            reader.dataset.noteId = note.id;
            reader.setAttribute('aria-label', `Selected knowledge note: ${note.title || note.id}`);
            const crumbs = el('nav', 'brain-crumbs'); crumbs.setAttribute('aria-label', 'Selected note hierarchy');
            const ancestors = []; const visited = new Set([note.id]); let parentId = edges.find(edge => edge.kind === 'hierarchy' && edge.target === note.id)?.source;
            while (parentId && !visited.has(parentId)) { ancestors.unshift(byId.get(parentId)); visited.add(parentId); parentId = edges.find(edge => edge.kind === 'hierarchy' && edge.target === parentId)?.source; }
            for (const ancestor of [...ancestors, note]) {
                if (crumbs.children.length) crumbs.append(el('span', '', '/'));
                const button = el('button', '', ancestor.title || ancestor.id); button.type = 'button'; button.addEventListener('click', () => select(ancestor.id, true)); crumbs.append(button);
            }
            const metadata = el('p', 'brain-note-meta', `${branchInfo(note.branch).label} · ${text(note.status) || 'Status unknown'} · reviewed ${text(note.reviewed) || 'unknown'}`);
            const source = resolveLink(`/${note.id}`, note.id, new Set(), data.source_commit);
            if (source) { const link = el('a', '', 'View Markdown ↗'); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; metadata.append(link); }
            reader.append(crumbs, metadata);
            if (!matches.has(note.id)) reader.append(el('p', 'brain-selected-outside', 'This selected note is outside the current filters. Reset filters to see its place in the full grove.'));
            const markdown = el('div', 'brain-markdown');
            if (note.content) renderMarkdown(markdown, note.content, context(note));
            else add(markdown, el('h2', '', note.title || note.id), el('p', '', note.summary || 'The note text is not included in this export.'));
            reader.append(markdown);
            if (array(note.sources).length) {
                const details = add(el('details', 'brain-source-links'), el('summary', '', `Source references (${note.sources.length})`));
                const list = el('ul');
                for (const source of note.sources) {
                    const item = el('li');
                    renderInline(item, `[${text(source.label).replace(/[\[\]]/g, '')}](${text(source.url)})`, context(note)); list.append(item);
                }
                reader.append(add(details, list));
            }
            reader.scrollTop = previousScroll;
        }
        function renderMap(matches) {
            const wrapper = el('div', 'brain-map');
            const svg = svgEl('svg', { viewBox: '0 0 840 630', role: 'group', 'aria-label': 'Interactive Markdown hierarchy. Tab between notes and press Enter to read. A searchable Notes view is also available.' });
            const layout = layoutGraph(nodes, rootId);
            const selected = state.selected;
            for (const edge of edges) {
                if (edge.kind !== 'hierarchy' && !state.references) continue;
                const from = layout.get(edge.source); const to = layout.get(edge.target); if (!from || !to) continue;
                const linked = edge.source === selected || edge.target === selected;
                const bendX = (from.x + to.x) / 2; const bendY = (from.y + to.y) / 2;
                svg.append(svgEl('path', { d: `M ${from.x} ${from.y} Q ${bendX + (315 - bendY) * .14} ${bendY} ${to.x} ${to.y}`, class: `brain-edge ${edge.kind === 'reference' ? 'brain-reference' : ''} ${linked ? 'brain-linked' : ''}`, 'aria-hidden': 'true' }));
            }
            const visibleNodes = [];
            for (const note of nodes) {
                const point = layout.get(note.id); if (!point) continue;
                const matching = matches.has(note.id);
                const group = svgEl('g', { transform: `translate(${point.x}, ${point.y})`, role: 'button', tabindex: matching ? '0' : '-1', 'aria-label': `${note.title || note.id}. ${array(note.todos).filter(todo => !isDone(todo)).length} open TODOs.`, 'aria-pressed': String(note.id === selected), class: `brain-node ${point.root ? 'brain-root' : ''} ${note.id === selected ? 'brain-selected' : ''} ${matching ? '' : 'brain-dim'}` });
                group.style.setProperty('--branch-color', branchInfo(note.branch).color);
                group.append(svgEl('title', {}, `${note.title || note.id}\n${note.summary || ''}`));
                group.append(svgEl('rect', { x: -74, y: -26, width: 148, height: 52, rx: point.root ? 16 : 8 }));
                const title = text(note.title || note.id); const words = title.split(/\s+/); const lines = [''];
                for (const word of words) {
                    if ((lines.at(-1) + ' ' + word).trim().length > 23 && lines.at(-1)) lines.push(word); else lines[lines.length - 1] = `${lines.at(-1)} ${word}`.trim();
                }
                const firstLine = lines[0].length > 23 ? `${lines[0].slice(0, 21)}…` : lines[0];
                group.append(svgEl('text', { 'text-anchor': 'middle', y: -4 }, firstLine));
                const second = lines.length > 1 ? `${lines[1].slice(0, 22)}${lines.length > 2 || lines[1].length > 22 ? '…' : ''}` : point.root ? 'Start here' : `${array(note.todos).filter(todo => !isDone(todo)).length} open TODOs`;
                group.append(svgEl('text', { 'text-anchor': 'middle', y: 13, class: 'brain-node-sub' }, second));
                group.addEventListener('click', () => select(note.id, true));
                group.addEventListener('keydown', event => {
                    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(note.id, true); }
                    else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
                        event.preventDefault(); const index = visibleNodes.indexOf(group); const direction = ['ArrowRight', 'ArrowDown'].includes(event.key) ? 1 : -1;
                        visibleNodes[(index + direction + visibleNodes.length) % visibleNodes.length]?.focus();
                    }
                });
                if (matching) visibleNodes.push(group);
                svg.append(group);
            }
            wrapper.append(svg);
            const legend = el('div', 'brain-map-footer');
            legend.append(el('span', '', 'Solid links: hierarchy · dashed links: references. Dim notes provide context.'));
            const label = el('label'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = state.references;
            checkbox.addEventListener('change', () => {
                state.references = checkbox.checked; refresh();
                canvasHost.querySelector('.brain-map-footer input')?.focus({ preventScroll: true });
            });
            legend.append(add(label, checkbox, document.createTextNode('Show references')));
            canvasHost.append(add(wrapper, legend));
        }
        function renderNotes(filtered) {
            const results = el('div', 'brain-results');
            for (const note of filtered) {
                const card = el('button', 'brain-note-card'); card.type = 'button'; card.style.setProperty('--branch-color', branchInfo(note.branch).color); card.setAttribute('aria-pressed', String(note.id === state.selected));
                const open = array(note.todos).filter(todo => !isDone(todo)).length;
                add(card, el('strong', '', note.title || note.id), el('p', '', note.summary || 'No summary available.'), el('small', '', `${branchInfo(note.branch).label} · ${open} open TODOs · reviewed ${text(note.reviewed) || 'unknown'}`));
                card.addEventListener('click', () => select(note.id, true)); results.append(card);
            }
            if (!filtered.length) results.append(el('p', 'brain-empty', 'No notes match these filters.'));
            canvasHost.append(results);
        }
        function renderTasks(filtered) {
            const filters = el('div', 'brain-task-filter'); filters.setAttribute('aria-label', 'Filter TODO completion status');
            for (const [value, label] of [['open', 'Open'], ['done', 'Completed'], ['all', 'All TODOs']]) {
                const button = el('button', 'brain-pill', label); button.type = 'button'; button.id = `${prefix}-todo-${value}`; button.setAttribute('aria-pressed', String(state.status === value));
                button.addEventListener('click', () => {
                    state.status = value; refresh();
                    document.getElementById(`${prefix}-todo-${value}`)?.focus({ preventScroll: true });
                }); filters.append(button);
            }
            canvasHost.append(filters);
            const results = el('div', 'brain-results');
            for (const { node, todo } of filtered) {
                const card = el('div', 'brain-task-card'); card.style.setProperty('--branch-color', branchInfo(node.branch).color);
                const stateLabel = el('span', `brain-task-state ${isDone(todo) ? 'brain-done' : ''}`, isDone(todo) ? 'Completed' : 'Open');
                const task = el('p'); renderInline(task, todo.text, context(node));
                const origin = el('button', 'brain-task-origin', `Read ${node.title || node.id} →`); origin.type = 'button'; origin.addEventListener('click', () => select(node.id, true));
                add(results, add(card, stateLabel, task, origin));
            }
            if (!filtered.length) results.append(el('p', 'brain-empty', 'No TODOs match this view.'));
            canvasHost.append(results);
        }
        function refresh() {
            const filtered = filterNotes(nodes, state); const tasks = filterTodos(nodes, state);
            const matches = new Set((state.mode === 'todos' ? tasks.map(item => item.node) : filtered).map(note => note.id));
            for (const [mode, button] of tabs) button.setAttribute('aria-pressed', String(state.mode === mode));
            for (const [branch, button] of branchButtons) button.setAttribute('aria-pressed', String(state.branch === branch));
            matchCount.textContent = state.mode === 'todos' ? `${tasks.length} TODOs` : `${filtered.length} / ${nodes.length} notes`;
            canvasHost.replaceChildren();
            if (state.mode === 'map') renderMap(matches); else if (state.mode === 'notes') renderNotes(filtered); else renderTasks(tasks);
            renderReader(matches);
        }
        search.addEventListener('input', () => { state.query = search.value; refresh(); });
        reset.addEventListener('click', () => { state.query = ''; state.branch = 'all'; state.status = 'open'; search.value = ''; refresh(); });
        refresh();
    }

    async function mount(host, options = {}) {
        if (!host) throw new Error('A host element is required.');
        host.classList.add('tg-brain'); host.setAttribute('aria-busy', 'true');
        host.replaceChildren(el('p', 'brain-empty', 'Loading the knowledge grove…'));
        try {
            const response = await fetch(options.url || 'brain.json');
            if (!response.ok) throw new Error(`Knowledge export unavailable (HTTP ${response.status}).`);
            render(host, await response.json());
        } catch (error) {
            const box = add(el('section', 'brain-shell brain-error'), el('h2', '', 'Knowledge grove unavailable'), el('p', '', text(error.message)), el('p', '', 'The analysis dashboard remains usable. Regenerate the knowledge export to include the Markdown notes and TODOs.'));
            box.setAttribute('role', 'alert');
            const retry = el('button', '', 'Retry knowledge export'); retry.type = 'button'; retry.addEventListener('click', () => mount(host, options));
            host.replaceChildren(add(box, retry));
        } finally { host.setAttribute('aria-busy', 'false'); }
    }

    const api = { mount };
    if (typeof module !== 'undefined' && module.exports) module.exports = { ...api, resolveLink, tokenizeInline, markdownBlocks, filterNotes, filterTodos, layoutGraph };
    if (global) global.TraceGroveBrain = api;
})(typeof window !== 'undefined' ? window : null);
