/* Current-chain observations and resilient, non-overlapping snapshot polling. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.TraceGroveLive = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';
    const INTERVAL = 60000;
    const numeric = n => typeof n === 'number' && Number.isFinite(n);
    const fmt = n => numeric(n) ? n.toLocaleString() : 'Unknown';
    const rows = v => Array.isArray(v) ? v : [];
    function freshness(value, now = Date.now(), interval = 120) {
        const time = typeof value === 'string' ? Date.parse(value) : NaN;
        if (!Number.isFinite(time)) return {state: 'unknown', seconds: null};
        const seconds = (now - time) / 1000;
        return {state: seconds < -60 ? 'clock mismatch' : seconds > Math.max(600, interval * 3) ? 'stale' : 'recent', seconds: Math.max(0, Math.floor(seconds))};
    }
    async function fetchJSON(url) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 20000);
        try {
            const response = await fetch(url, {cache: 'no-store', signal: controller.signal});
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            return await response.json();
        } finally { clearTimeout(timeout); }
    }
    function createPoller({load, onData, onStatus = () => {}, visible = () => true}) {
        let busy = false, stopped = false, previous = null;
        return {
            async check() {
                if (busy || stopped || !visible()) return false;
                busy = true;
                try {
                    const data = await load();
                    if (stopped) return false;
                    const key = JSON.stringify(data);
                    const changed = key !== previous;
                    if (changed) { await onData(data); previous = key; }
                    onStatus({ok: true, changed, checkedAt: new Date().toISOString()});
                    return changed;
                } catch (error) {
                    if (!stopped) onStatus({ok: false, error: error.message || String(error), hasSnapshot: previous !== null});
                    return false;
                } finally { busy = false; }
            },
            stop() { stopped = true; },
        };
    }
    function startPolling(options) {
        const poller = createPoller({...options, visible: () => !document.hidden});
        const timer = setInterval(() => poller.check(), INTERVAL);
        const resume = () => { if (!document.hidden) poller.check(); };
        document.addEventListener('visibilitychange', resume);
        poller.check();
        return {check: () => poller.check(), stop() {clearInterval(timer); document.removeEventListener('visibilitychange', resume); poller.stop();}};
    }
    function el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined) n.textContent = text;
        return n;
    }
    function mount(host) {
        host.replaceChildren();
        const title = el('h2', '', 'Current-chain observations');
        const intro = el('p', 'hint', 'A separate observer samples recent confirmed blocks from a public RPC node. This feed measures chain activity; it does not resolve their rings.');
        const status = el('p', 'feed-status', 'Checking the current-chain feed…');
        status.setAttribute('role', 'status');
        const content = el('div');
        host.append(title, intro, status, content);
        const mirror = location.hostname.endsWith('.github.io');
        if (mirror) {
            const note = el('p', 'notice info', 'GitHub Pages serves a published snapshot. For automatically arriving observations, open ');
            const link = el('a', '', 'the live dashboard ↗'); link.href = 'http://35.254.148.94/#live-observations'; note.append(link, '.'); host.insertBefore(note, content);
        }
        let lastData = null;
        function render(data) {
            if (!data || data.schema_version !== 1 || !Array.isArray(data.blocks)) throw new Error('The observer export has an unsupported format');
            lastData = data;
            content.replaceChildren();
            const meta = el('p', 'hint');
            meta.textContent = `Last successful poll: ${data.last_success_at || 'not yet recorded'} · Source: ${data.source?.origin || data.source_host || 'public RPC'} · First retained block observed: ${data.blocks[0]?.observed_at || 'unknown'}`;
            content.append(meta);
            const grid = el('div', 'feed-metrics');
            const latest = data.blocks[data.blocks.length - 1];
            const totals = data.blocks.reduce((acc,b) => ({tx: acc.tx + (numeric(b.transaction_count) ? b.transaction_count : 0), rings: acc.rings + (numeric(b.ring_input_count) ? b.ring_input_count : 0)}), {tx: 0, rings: 0});
            const items = [['Observed chain tip', data.chain_tip?.height], ['Latest sampled height', data.last_processed_height ?? latest?.height], ['Blocks retained', data.blocks.length], ['Transactions in these blocks', totals.tx], ['Input rings counted', data.blocks.some(b => numeric(b.ring_input_count)) ? totals.rings : null]];
            for (const [label, value] of items) {const card = el('div', 'metric');card.append(el('strong', '', fmt(value)), el('span', '', label));grid.append(card);}
            content.append(grid);
            content.append(el('p', 'hint', `${fmt(data.window?.blocks_with_ring_counts)} of ${fmt(data.blocks.length)} displayed blocks have decoded ring counts. Confirmations behind observed tip: ${fmt(data.limits?.confirmations)}.`));
            content.append(el('p', 'hint', 'Transactions exclude mining transactions. Counts cover the retained sampled blocks only; ring counts can be incomplete when block details exceed the observation limits. No spent-output or wallet-address labels are inferred.'));
            const blocks = data.blocks.slice(-48);
            if (blocks.length) {
                const chart = el('div', 'feed-bars'); chart.setAttribute('role', 'img'); chart.setAttribute('aria-label', 'Transaction count per sampled block, oldest to newest. Exact values are in the table below.');
                const max = Math.max(1, ...blocks.map(b => numeric(b.transaction_count) ? b.transaction_count : 0));
                blocks.forEach(b => {const bar = el('span', 'feed-bar');bar.style.height = `${Math.max(2, 100 * (b.transaction_count || 0) / max)}%`;bar.title = `Block ${b.height}: ${fmt(b.transaction_count)} transactions`;chart.append(bar);});
                content.append(chart, el('p', 'hint', `Transaction activity · last ${blocks.length} sampled blocks · oldest → newest`));
                const details = el('details'); details.append(el('summary', '', 'View sampled block timeline'));
                const wrap = el('div', 'table-scroll'); wrap.tabIndex = 0; wrap.setAttribute('aria-label', 'Sampled block data');
                const table = el('table'); const header = el('tr');
                ['Height', 'Chain timestamp (UTC)', 'First observed (UTC)', 'Transactions', 'Input rings', 'Ring memberships'].forEach(s => {const th=el('th','',s);th.scope='col';header.append(th);});
                const head=el('thead');head.append(header);table.append(head);
                const body=el('tbody');
                [...data.blocks].reverse().forEach(b => {const tr=el('tr');[fmt(b.height), b.chain_timestamp || 'Unknown', b.observed_at || 'Unknown', fmt(b.transaction_count), fmt(b.ring_input_count), fmt(b.ring_member_count)].forEach(v => tr.append(el('td','',v)));body.append(tr);});
                table.append(body);wrap.append(table);details.append(wrap);content.append(details);
            } else content.append(el('p', 'hint', 'No complete sampled blocks have been published yet.'));
            const gaps = rows(data.gaps);
            if (gaps.length) content.append(el('p', 'notice', `${gaps.length} recorded observation gaps. This is a bounded observer, not a complete chain archive; consult the raw export for skipped ranges.`));
            if (data.state === 'error' || data.error) content.append(el('p', 'notice', `Observer reports an error: ${data.error?.message || 'check collector logs'}. Previously sampled blocks remain visible.`));
            const raw = el('a', 'hint', 'Download current-chain observations JSON'); raw.href='live-observations.json';content.append(raw);
        }
        const polling = startPolling({load: () => fetchJSON('live-observations.json'), onData: render, onStatus(result) {
            host.setAttribute('aria-busy', 'false');
            if (!result.ok) {status.textContent = `Feed check failed (${result.error}). ${lastData ? 'Keeping the last displayed observations.' : 'No observer snapshot is available here yet.'} Retrying every 60 seconds.`;return;}
            const age = freshness(lastData?.last_success_at, Date.now(), lastData?.limits?.interval_seconds || 120);
            status.textContent = `${mirror ? 'Published mirror' : 'Observer'} · ${lastData?.state || 'unknown'} · ${age.state}${age.seconds === null ? '' : ` (${fmt(age.seconds)} seconds since success)`} · page checks every 60 seconds`;
        }});
        return polling;
    }
    return {INTERVAL, freshness, fetchJSON, createPoller, startPolling, mount};
});
