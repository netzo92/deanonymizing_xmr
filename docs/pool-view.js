/* Aggregate-only prospective pool observations. No transaction identities are rendered. */
(function (root, factory) {
    const api = factory(root);
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {root.TraceGrovePool = api;const host = document.getElementById('pool-root');if (host) api.page = api.mount(host);}
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
    'use strict';
    const array = value => Array.isArray(value) ? value : [];
    const count = value => Number.isSafeInteger(value) && value >= 0;
    const nonnegative = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
    const fmt = value => count(value) ? value.toLocaleString() : 'Unknown';
    const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    function integer(value) {
        if (typeof value === 'string' && /^(0|[1-9]\d*)$/.test(value)) return value;
        return count(value) ? String(value) : null;
    }
    function formatInteger(value) {const n = integer(value);return n === null ? 'Unknown' : BigInt(n).toLocaleString();}
    function formatXMR(value) {
        const n = integer(value);if (n === null) return 'Unknown';
        const digits = n.padStart(13, '0'), whole = digits.slice(0, -12), fraction = digits.slice(-12).replace(/0+$/, '');
        return `${BigInt(whole).toLocaleString()}${fraction ? '.' + fraction : ''}`;
    }
    function feeDensity(fee, weight) {
        const f = integer(fee), w = integer(weight);if (f === null || w === null || BigInt(w) === 0n) return null;
        const value = BigInt(f) * 100n / BigInt(w), whole = value / 100n, fraction = String(value % 100n).padStart(2, '0');
        return value === 0n && BigInt(f) > 0n ? '<0.01' : `${whole.toLocaleString()}.${fraction}`;
    }
    function duration(seconds) {
        if (!nonnegative(seconds)) return 'Unknown';
        if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
        return `${Math.floor(seconds / 86400)}d ${Math.floor(seconds % 86400 / 3600)}h`;
    }
    function date(value) {const stamp = typeof value === 'string' ? Date.parse(value) : NaN;return Number.isFinite(stamp) ? new Date(stamp).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC') : 'Unknown';}
    function sourceSummary(source = {}) {
        const privateNode = ['private_node', 'private_validating_node', 'local_node'].includes(source.mode);
        const label = source.mode === 'public_rpc' ? 'Public RPC observation point' : privateNode ? 'Private Monero node' : 'Observation source not established';
        const bootstrap = source.bootstrap === true || source.bootstrap === 'active';
        const sync = bootstrap ? 'Bootstrap data in use' : source.synchronized === true ? 'Node reports synchronized' : source.synchronized === false ? 'Node is synchronizing' : 'Synchronization unknown';
        return {label, sync, privateNode, warning: bootstrap || source.synchronized !== true, interpretation: privateNode ? 'A dedicated node provides this observation point. The sync state is node-reported.' : 'The external provider supplies this pool view. TraceGrove does not independently validate the chain through this feed.'};
    }
    function pollKind(poll) {
        if (poll.state === 'error' || poll.state === 'failed' || poll.error_type) return poll.pool_complete === true && count(poll.pool_count) ? 'followup_failed' : 'failed';
        if (poll.pool_complete === false) return 'partial';
        if (poll.pool_complete === true && count(poll.pool_count)) return 'complete';
        return 'unknown';
    }
    function delaySummary(value) {
        if (!object(value) || !count(value.sample_count)) return {state: 'unknown', reason: 'A delay sample has not been published.'};
        const bins = array(value.bins);
        if (bins.some(bin => !object(bin) || typeof bin.label !== 'string' || !count(bin.count)) || bins.reduce((sum,bin) => sum + bin.count, 0) !== value.sample_count) return {state: 'unknown', reason: 'The published delay bins do not reconcile with their sample count.'};
        return {state: value.sample_count > 0 ? 'measured' : 'empty', sampleCount: value.sample_count, excludedCount: value.excluded_count, median: value.median_seconds, p90: value.p90_seconds, bins, basis: value.basis};
    }
    function validateSnapshot(data) {
        if (!object(data) || data.schema_version !== 1 || !['warming_up', 'observing', 'error', 'paused'].includes(data.state)) throw Error('The pool export is missing or uses an unsupported format');
        for (const key of ['source', 'current', 'coverage', 'outcomes', 'limits']) if (data[key] !== undefined && data[key] !== null && !object(data[key])) throw Error(`Invalid ${key} metadata`);
        if (data.poll_history !== undefined && (!Array.isArray(data.poll_history) || data.poll_history.some(poll => !object(poll)))) throw Error('Invalid poll history');
        const current = data.current || {};
        for (const key of ['transaction_count', 'reported_pool_count', 'receive_time_known', 'receive_time_unknown']) if (current[key] !== undefined && current[key] !== null && !count(current[key])) throw Error('Invalid current-pool count');
        for (const key of ['fee_atomic_total', 'weight_total']) if (current[key] !== undefined && current[key] !== null && integer(current[key]) === null) throw Error('Invalid pool fee or weight');
        if (current.complete !== undefined && current.complete !== null && typeof current.complete !== 'boolean') throw Error('Invalid pool completeness flag');
        if (['receive_time_known', 'receive_time_unknown', 'transaction_count'].every(key => count(current[key])) && current.receive_time_known + current.receive_time_unknown !== current.transaction_count) throw Error('Receipt-time availability counts do not reconcile');
        if (current.untracked_due_limit !== undefined && current.untracked_due_limit !== null && (!count(current.untracked_due_limit) || count(current.transaction_count) && current.untracked_due_limit > current.transaction_count)) throw Error('Tracking-limit count exceeds the pool snapshot');
        const coverage = data.coverage || {};
        for (const key of ['polls_total', 'successful_polls', 'failed_polls']) if (coverage[key] !== undefined && coverage[key] !== null && !count(coverage[key])) throw Error('Invalid poll coverage count');
        if (['polls_total', 'successful_polls', 'failed_polls'].every(key => count(coverage[key])) && coverage.successful_polls + coverage.failed_polls !== coverage.polls_total) throw Error('Poll outcome counts do not reconcile');
        const outcomes = data.outcomes || {};
        for (const key of ['tracked_transactions', 'pending', 'disappeared', 'confirmed', 'censored', 'cold_start_transactions', 'followup_censored_transactions', 'observed_confirmations', 'confirmations_seen_before_block', 'confirmed_block_transactions']) if (outcomes[key] !== undefined && outcomes[key] !== null && !count(outcomes[key])) throw Error('Invalid observation outcome count');
        const partition = ['pending', 'disappeared', 'confirmed', 'censored'];
        if (count(outcomes.tracked_transactions) && partition.every(key => count(outcomes[key])) && partition.reduce((total,key) => total + outcomes[key], 0) !== outcomes.tracked_transactions) throw Error('Tracked transaction states do not reconcile');
        for (const [a,b] of [['cold_start_transactions','tracked_transactions'], ['followup_censored_transactions','tracked_transactions'], ['confirmations_seen_before_block','observed_confirmations'], ['observed_confirmations','confirmed_block_transactions']]) if (count(outcomes[a]) && count(outcomes[b]) && outcomes[a] > outcomes[b]) throw Error('Observation cohort counts do not reconcile');
        const delay = outcomes.confirmation_delay;
        if (delay !== undefined && delay !== null) {
            if (!object(delay)) throw Error('Invalid delay metadata');
            for (const key of ['sample_count', 'excluded_count']) if (delay[key] !== undefined && delay[key] !== null && !count(delay[key])) throw Error('Invalid delay sample count');
            if (count(delay.sample_count)) {
                if (delaySummary(delay).state === 'unknown') throw Error('Delay bins do not reconcile with their sample');
                for (const key of ['median_seconds', 'p90_seconds']) if (delay[key] !== undefined && (delay.sample_count > 0 ? !nonnegative(delay[key]) : delay[key] !== null)) throw Error('Invalid delay percentile for the available sample');
                if (nonnegative(delay.median_seconds) && nonnegative(delay.p90_seconds) && delay.p90_seconds < delay.median_seconds) throw Error('Delay percentiles are out of order');
                if (count(delay.excluded_count) && count(outcomes.confirmed) && delay.sample_count + delay.excluded_count !== outcomes.confirmed) throw Error('Delay eligibility does not reconcile with confirmed records');
            }
        }
        return data;
    }
    function derive(data) {
        const current = data.current || {}, outcomes = data.outcomes || {}, coverage = data.coverage || {};
        const notices = [];
        if (data.state === 'warming_up') notices.push('The pilot is warming up. Existing pool entries can predate this observation session; their earlier history is unknown.');
        if (data.state === 'paused') notices.push('Observation is paused. The last retained snapshot remains visible; new outcomes are not being established.');
        if (data.state === 'error' || data.error) notices.push(`The observer reports an error${data.error?.message ? ': ' + data.error.message : '.'} Retained observations are not a current successful poll.`);
        if (current.complete === false) notices.push('This pool snapshot is incomplete or limited. Returned counts, fees and weights describe the visible subset; absence cannot be established from this snapshot.');
        if (current.complete !== true && current.complete !== false) notices.push('Pool snapshot completeness has not been established. Missing entries must not be interpreted as absent transactions.');
        if (current.tracking_complete === false || count(current.untracked_due_limit) && current.untracked_due_limit > 0) notices.push(`Tracking capacity was reached. ${fmt(current.untracked_due_limit)} returned entries were not added to follow-up; a complete pool response does not imply complete tracking coverage.`);
        if (coverage.initial_window_left_truncated === true) notices.push('The pilot starts partway through chain history. Earlier sightings and outcomes are outside its observation window.');
        if (array(data.gaps).length) notices.push(`${fmt(data.gaps.length)} collection ${data.gaps.length === 1 ? 'gap is' : 'gaps are'} recorded in this export. Missing intervals limit observation coverage.`);
        return {current, outcomes, coverage, source: sourceSummary(data.source || {}), notices, delay: delaySummary(outcomes.confirmation_delay), polls: array(data.poll_history), density: feeDensity(current.fee_atomic_total, current.weight_total)};
    }
    function el(tag, className, text) {const node = document.createElement(tag);if (className) node.className = className;if (text !== undefined) node.textContent = String(text);return node;}
    function add(node, ...children) {node.append(...children.filter(Boolean));return node;}
    function link(label, href) {const node = el('a', '', label);node.href = href;return node;}
    function facts(values) {const dl = el('dl', 'pool-facts');values.forEach(([label,value]) => dl.append(el('dt', '', label), el('dd', '', value)));return dl;}
    function metric(label, value, note) {return add(el('div', 'pool-metric'), el('span', 'metric-label', label), el('strong', '', value), el('small', '', note));}
    function sectionHead(title, description) {return add(el('div', 'pool-section-head'), add(el('div'), el('h2', '', title), el('p', '', description)));}
    function details(title, id) {const node = add(el('details', 'pool-details'), el('summary', '', title));node.dataset.disclosure = id;return node;}
    function renderSource(data, model) {
        const {source} = model;
        const card = el('section', `pool-source${source.warning ? ' is-warning' : ''}`);card.setAttribute('aria-label', 'Observation source and synchronization');
        card.append(add(el('div'), el('p', 'pool-kicker', 'One configured observation source'), el('h2', '', source.label), el('span', 'pool-state', source.sync), el('p', '', source.interpretation)));
        card.append(add(el('div'), el('span', 'pool-source-label', 'Current source'), el('strong', '', data.source?.network ? `${data.source.network} · ${data.source.node_version || 'version unknown'}` : 'Network and version unknown'), el('p', '', data.source?.origin || 'Source origin not published'), el('p', '', data.source?.restricted === true ? 'Restricted RPC · individual node receipt times may be hidden' : data.source?.restricted === false ? 'Unrestricted RPC · available metadata varies by relay state' : 'RPC restriction status unknown')));
        card.append(add(el('div'), el('span', 'pool-source-label', 'Last successful observation'), el('strong', '', date(data.last_success_at)), el('p', '', `Session started ${date(model.coverage.session_started_at)}`), el('p', '', `Observer state: ${data.state.replaceAll('_', ' ')}`)));
        return card;
    }
    function renderPolls(model) {
        const section = el('section', 'pool-section');section.id = 'pool-coverage';
        section.append(sectionHead('How much are we observing?', 'Pool snapshots and block follow-up have separate limits. Failed polls and incomplete responses remain visible.'));
        const grid = el('div', 'pool-grid'), history = el('article', 'pool-card');history.append(el('h3', '', 'Recent pool snapshots'));
        const polls = model.polls.slice(-60);
        if (polls.length) {
            history.append(el('p', '', `Showing the latest ${fmt(polls.length)} polls in this published window, oldest to newest. Each bar is the number of transactions this node returned. An error marker means the count is unknown, not zero.`));
            const bars = el('div', 'pool-poll-bars');bars.setAttribute('role', 'img');bars.setAttribute('aria-label', 'Pool count across recent polls. Colors distinguish complete, partial, failed and unknown observations; exact values are in the poll table.');
            const max = Math.max(1, ...polls.filter(poll => pollKind(poll) !== 'failed' && count(poll.pool_count)).map(poll => poll.pool_count));
            polls.forEach(poll => {const kind = pollKind(poll), bar = el('span', `pool-poll-bar is-${kind}`);bar.style.height = `${kind === 'failed' || !count(poll.pool_count) ? 9 : Math.max(2, 100 * poll.pool_count / max)}%`;bar.title = `${date(poll.started_at)} · ${kind} · count ${fmt(poll.pool_count)}`;bars.append(bar);});
            history.append(bars, add(el('div', 'pool-bar-axis'), el('span', '', date(polls[0].started_at)), el('span', '', date(polls.at(-1).started_at))));
            history.append(add(el('div', 'pool-legend'), el('span', '', 'Complete'), el('span', '', 'Partial'), el('span', '', 'Pool fetch failed'), el('span', '', 'Unknown'), el('span', '', 'Pool seen; round failed')));
        } else history.append(add(el('div', 'pool-delay-empty'), el('h4', '', 'Waiting for the first published polls'), el('p', '', 'A missing history is not an empty transaction pool. The pilot will build this view as observations are recorded.')));
        const coverage = model.coverage;
        const quality = add(el('article', 'pool-card'), el('h3', '', 'Observation and follow-up coverage'), facts([['Recorded collection rounds', fmt(coverage.polls_total)], ['Rounds completed', fmt(coverage.successful_polls)], ['Rounds with errors', fmt(coverage.failed_polls)], ['Observer sessions', fmt(coverage.sessions_total)], ['Block follow-up starts at', fmt(coverage.initial_block_from_height)], ['Last block checked', fmt(coverage.last_processed_height)], ['Blocks behind follow-up target', fmt(coverage.blocks_behind)]]), el('p', 'pool-small', 'A collection round includes pool fetches and block follow-up. A valid pool snapshot can survive a later block-check failure. These counts measure collection health, not network-wide transaction coverage; short-lived entries can fall between polls.'));
        grid.append(history, quality);section.append(grid);
        const tableDetails = details('Exact poll history & collection gaps', 'poll-history');
        if (model.polls.length) {
            const table = el('table');table.append(el('caption', '', 'Published pool polls. The history is bounded by the observer export limits.'));
            const head = el('thead'), tr = el('tr');['Poll started (UTC)', 'Outcome', 'Pool count', 'Collection-round duration', 'Complete snapshot'].forEach(label => {const th = el('th', '', label);th.scope = 'col';tr.append(th);});head.append(tr);table.append(head);
            const body = el('tbody');[...model.polls].reverse().forEach(poll => {const row = el('tr');[date(poll.started_at), pollKind(poll) === 'followup_failed' ? 'Pool seen; round failed' : pollKind(poll), pollKind(poll) === 'failed' ? 'Unknown' : fmt(poll.pool_count), duration(poll.duration_seconds), poll.pool_complete === true ? 'Yes' : poll.pool_complete === false ? 'No' : 'Unknown'].forEach(value => row.append(el('td', '', value)));body.append(row);});table.append(body);tableDetails.append(add(el('div', 'pool-table-wrap'), table));
        }
        if (array(model.gaps).length) model.gaps.forEach(gap => tableDetails.append(el('p', '', `${gap.kind || 'Gap'} · ${date(gap.recorded_at)} · heights ${fmt(gap.from_height)}–${fmt(gap.to_height)}`)));
        else tableDetails.append(el('p', '', 'No collection-gap records are included in this export. This does not establish uninterrupted observation before the retained window.'));
        section.append(tableDetails);return section;
    }
    function renderOutcomes(model) {
        const outcomes = model.outcomes, section = el('section', 'pool-section');section.id = 'pool-outcomes';
        section.append(sectionHead('What happened to the transactions we followed?', 'A block match establishes an observed confirmation. A transaction leaving this node’s pool may have many causes.'));
        const outcomeGrid = el('div', 'pool-outcomes');
        [['confirmed', 'Matched to a block', 'Observed confirmation, subject to the recorded block follow-up.'], ['pending', 'Still pending', 'Tracked and still present in the observed pool.'], ['disappeared', 'Currently absent', 'Not seen in a complete pool view; no confirmation has been established.'], ['censored', 'Follow-up ended', 'The observation window closed without an established outcome.']].forEach(([key,label,note]) => outcomeGrid.append(add(el('article', 'pool-outcome'), el('span', '', label), el('strong', '', fmt(outcomes[key])), el('p', '', note))));
        section.append(outcomeGrid);
        const grid = el('div', 'pool-grid');grid.style.marginTop = '18px';
        grid.append(add(el('article', 'pool-card'), el('h3', '', 'Keep the cohort visible'), facts([['Retained tracked transactions', fmt(outcomes.tracked_transactions)], ['Cold-start or resumed-observation entries', fmt(outcomes.cold_start_transactions)], ['Records with interrupted follow-up', fmt(outcomes.followup_censored_transactions)], ['All transactions in followed blocks', fmt(outcomes.confirmed_block_transactions)], ['Pool-tracked matches in those blocks', fmt(outcomes.observed_confirmations)], ['Matches at a later block height', fmt(outcomes.confirmations_seen_before_block)]]), el('p', '', 'Cold-start entries may already have waited before observation began or resumed. They are not fresh arrivals. A transaction missing from the pool is not automatically confirmed or failed.'), el('p', 'pool-small', 'The stricter later-height subset requires the confirming block to be above the source’s chain tip at first pool sighting. It does not reveal transaction creation time. Cold-start and interrupted-follow-up counts overlap the outcome states.'), el('p', 'pool-small', 'Transaction states describe retained tracked records; block-match counts cover retained followed blocks. These rolling populations can differ and are not a complete network census.')));
        const delay = model.delay, delayCard = add(el('article', 'pool-card'), el('h3', '', 'Time to observed block detection'));
        if (delay.state === 'measured') {
            delayCard.append(el('p', '', 'Elapsed time from first local pool fetch to local confirmed-block detection. It includes the polling interval and configured confirmation lag.'), facts([['Eligible confirmed samples', fmt(delay.sampleCount)], ['Median observed interval', duration(delay.median)], ['90th-percentile observed interval', duration(delay.p90)], ['Excluded from this delay sample', fmt(delay.excludedCount)]]));
            const chart = el('div', 'pool-delay'), maximum = Math.max(1, ...delay.bins.map(bin => bin.count));
            delay.bins.forEach(bin => {const fill = el('div', 'pool-delay-fill');fill.style.width = `${100 * bin.count / maximum}%`;fill.setAttribute('aria-hidden', 'true');chart.append(add(el('div', 'pool-delay-row'), el('span', '', bin.label), add(el('div', 'pool-delay-track'), fill), el('strong', '', fmt(bin.count))));});delayCard.append(chart);
            delayCard.append(el('p', 'pool-small', `Histogram denominator: ${fmt(delay.sampleCount)} eligible intervals. It describes observed confirmations, not all pending or censored transactions.`));
        } else {
            delayCard.append(add(el('div', 'pool-delay-empty'), el('h4', '', delay.state === 'empty' ? 'No eligible confirmation intervals yet' : 'Delay measurement unavailable'), el('p', '', delay.state === 'empty' ? 'The pilot needs a block match for a transaction first observed during a continuing session. Until then, there is no measured delay distribution to display.' : delay.reason)));
            if (delay.state === 'empty') delayCard.append(facts([['Eligible confirmed samples', '0'], ['Excluded from this delay sample', fmt(delay.excludedCount)]]));
        }
        delayCard.append(el('p', '', 'Only eligible same-session, non-cold-start observations enter the monotonic-time delay sample. These intervals are neither wallet-submission times nor forecast results.'));
        grid.append(delayCard);section.append(grid);return section;
    }
    function renderTiming(data, model) {
        const card = el('section', 'pool-section');card.id = 'pool-timing';
        card.append(sectionHead('Two different clocks', 'Our first successful pool fetch records a local sighting. A node receipt timestamp is separate, may change with relay state, and may be hidden by the RPC.'));
        const grid = el('div', 'pool-grid'), current = model.current;
        grid.append(add(el('article', 'pool-card'), el('h3', '', 'Local observation time'), el('p', '', 'The pilot records when its own requests return. A first sighting means “visible by this poll,” not “created or broadcast at this time.” Restarts, polling gaps and cold-start entries limit what can be inferred.'), el('p', 'pool-small', `Current snapshot fetched: ${date(current.snapshot_at)}. Delay samples include the extra wait before a block is detected at the configured confirmation depth.`)));
        grid.append(add(el('article', 'pool-card'), el('h3', '', 'Node receipt-time availability'), facts([['Returned entries with node receipt time', fmt(current.receive_time_known)], ['Returned entries with unknown receipt time', fmt(current.receive_time_unknown)], ['Returned-entry denominator', fmt(current.transaction_count)]]), el('p', '', 'Unknown or redacted timestamps are not zero-age arrivals. Even an available receipt time belongs to this node, not the whole network. No raw per-transaction timestamps or identities are shown here.')));
        card.append(grid);
        const provenance = details('Source, retained scope & measurement definitions', 'pool-provenance');
        const source = data.source || {};
        provenance.append(el('p', '', `Source cohort: ${source.cohort_id || 'unknown'} · mode ${source.mode || 'unknown'} · network ${source.network || 'unknown'} · node version ${source.node_version || 'unknown'}.`), el('p', '', `Observer revision: ${source.source_revision || 'unknown'} · observer SHA-256: ${source.observer_sha256 || 'unknown'}. Snapshot generated ${date(data.generated_at)}.`), el('p', '', `Delay basis: ${model.delay.basis || 'First local pool fetch to local confirmed-block detection; eligible same-session observations only.'}`), el('p', '', `This is a bounded rolling pilot, not a permanent study archive. Transactions removed by retention: ${fmt(model.coverage.pruned_transactions)}. Poll counters and the published poll-history window have different scopes.`));
        provenance.append(el('p', '', `Daemon instance identity: ${source.daemon_instance_identity || 'unknown'}. Node start time availability: ${source.node_start_time_known === true ? 'known' : source.node_start_time_known === false ? 'not available' : 'unknown'}. A public endpoint can serve different daemon instances; a shared URL alone does not establish one continuous node history.`));
        if (object(data.limits)) Object.entries(data.limits).filter(([,value]) => typeof value === 'number' || typeof value === 'boolean' || typeof value === 'string').forEach(([key,value]) => provenance.append(el('p', '', `${key.replaceAll('_', ' ')}: ${value}`)));
        if (array(data.interpretation).length) {const ul = el('ul');data.interpretation.filter(value => typeof value === 'string').forEach(value => ul.append(el('li', '', value)));provenance.append(ul);}
        provenance.append(add(el('p'), link('Download the aggregate snapshot →', 'pool-observations.json')));card.append(provenance);return card;
    }
    function archiveSummary(data, now = Date.now()) {
        if (!object(data) || data.schema_version !== 1 || !['archived', 'error'].includes(data.state) || !object(data.policy) || !count(data.policy.interval_seconds) || data.policy.interval_seconds < 1) throw Error('Invalid archive status');
        const latest = data.latest;
        if (latest !== undefined && latest !== null) {
            if (!object(latest) || latest.schema_version !== 1 || latest.scope !== 'retained_snapshot' || !object(latest.rows) || !object(latest.storage)) throw Error('Invalid retained archive summary');
            for (const key of ['transactions', 'observations', 'polls']) if (!count(latest.rows[key])) throw Error('Invalid archive row count');
            if (!count(latest.storage.database_bytes) || !Number.isFinite(Date.parse(data.last_success_at))) throw Error('Invalid archive size or time');
        }
        if (data.state === 'archived' && !latest) throw Error('Archive success requires a saved snapshot');
        const age = latest ? Math.max(0, (now - Date.parse(data.last_success_at)) / 1000) : null;
        return {latest, age, stale: age !== null && age > data.policy.interval_seconds + 3600, failed: data.state === 'error'};
    }
    function mountArchive(host, live, fetchJSON) {
        const section = el('section', 'pool-section');section.id = 'pool-archive';
        section.append(sectionHead('Preserving observations for research', 'A separate job freezes retained records every six hours on the existing server. Raw transaction and timing records stay private.'));
        const status = el('p', 'pool-small', 'Checking the archive status…'), content = el('div', 'pool-card');section.append(status, content);host.append(section);
        let last = null;
        const poller = live.startPolling({load: () => Promise.resolve(fetchJSON('pool-archive-status.json')).then(data => {archiveSummary(data);return data;}), onData(data) {
            const model = archiveSummary(data), latest = model.latest;
            const view = el('div');
            view.append(el('h3', '', model.failed ? 'Archive needs attention' : model.stale ? 'Last freeze is overdue' : 'Private snapshot preserved'));
            if (latest) view.append(facts([['Last successful freeze', date(data.last_success_at)], ['Retained transactions', fmt(latest.rows.transactions)], ['Sighting rows', fmt(latest.rows.observations)], ['Collection rounds retained', fmt(latest.rows.polls)], ['Frozen database size', `${(latest.storage.database_bytes / 1048576).toFixed(2)} MiB`]]));
            view.append(el('p', '', `Schedule: every ${duration(data.policy.interval_seconds)}. Storage cap: ${count(data.policy.max_archive_bytes) ? (data.policy.max_archive_bytes / 1073741824).toFixed(0) + ' GiB' : 'unknown'} / ${fmt(data.policy.max_archives)} snapshots. Archives stop at capacity; existing study records are not automatically deleted.`));
            if (model.failed) view.append(el('p', 'pool-notice', 'The last archive attempt failed. The last successful freeze remains listed; new retention protection is not established until the job succeeds.'));
            if (model.stale) view.append(el('p', 'pool-notice', 'No successful freeze has been published within the expected schedule. The live observation feed has a separate status.'));
            view.append(el('p', 'pool-small', 'Each freeze contains all records still retained at capture, including pending and censored cases. It is not a complete network census or proof that earlier records were preserved. Overlapping snapshots must be reconciled before a forecast study.'), link('Archive aggregate and retention diagnostics →', 'pool-archive-status.json'));
            content.replaceChildren(view);last = data;
        }, onStatus(result) {const overdue = last && archiveSummary(last).stale;if (overdue && content.querySelector('h3')) content.querySelector('h3').textContent = 'Last freeze is overdue';status.textContent = result.ok ? overdue ? 'Archive is overdue: the last successful freeze exceeds its expected schedule.' : 'Archive status checked · page checks every 60 seconds.' : `Archive status unavailable. ${last ? 'Keeping the last successful display.' : 'No archive success is established on this page yet.'} Retrying every 60 seconds.`;}});
        return poller;
    }
    function mount(host, options = {}) {
        const live = options.live || root.TraceGroveLive;
        const content = el('div'), status = el('p', '', 'Checking the pool observation snapshot…');status.setAttribute('role', 'status');
        content.id = 'pool-observations';
        const refresh = el('button', '', 'Refresh now');refresh.type = 'button';
        const mirror = root.location?.hostname?.endsWith('.github.io');
        host.replaceChildren(add(el('div', 'pool-refresh'), status, refresh), content);
        if (mirror) host.insertBefore(add(el('p', 'pool-notice info'), 'This GitHub Pages copy is a published snapshot. Incoming observations appear on ', link('the live pool pilot', 'http://35.254.148.94/pool.html'), '.'), content);
        let lastData = null;
        function render(data) {
            const checked = validateSnapshot(data), model = derive(checked);model.gaps = array(data.gaps);
            const active = host.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
            const open = new Set([...content.querySelectorAll('details[open]')].map(node => node.dataset.disclosure));
            const view = el('div');
            model.notices.forEach(note => view.append(el('p', 'pool-notice', note)));
            view.append(renderSource(data, model));
            const current = model.current, complete = current.complete === true;
            const metrics = el('div', 'pool-metrics');metrics.append(metric('Transactions returned', fmt(current.transaction_count), `Node reports ${fmt(current.reported_pool_count)} in its pool · ${complete ? 'complete returned snapshot' : 'completeness limited or unknown'}`), metric('Visible transaction weight', formatInteger(current.weight_total), 'Sum of returned transaction weights, not serialized bytes'), metric('Visible transaction fees', formatXMR(current.fee_atomic_total), 'XMR · fee total for returned entries'), metric('Fee per weight unit', model.density || 'Not available', 'Atomic fee units · weighted average of returned entries'));
            view.append(metrics, renderPolls(model), renderOutcomes(model), renderTiming(data, model));
            content.replaceChildren(view);
            content.querySelectorAll('details').forEach(node => {node.open = open.has(node.dataset.disclosure);node.querySelector('summary').dataset.focus = `summary-${node.dataset.disclosure}`;});
            content.querySelectorAll('a').forEach((node,index) => {node.dataset.focus = `link-${node.getAttribute('href')}-${index}`;});
            if (active) [...content.querySelectorAll('[data-focus]')].find(node => node.dataset.focus === active)?.focus({preventScroll: true});
            lastData = checked;
        }
        if (!live?.startPolling || !live?.fetchJSON) {
            host.setAttribute('aria-busy', 'false');refresh.disabled = true;
            status.textContent = 'The shared snapshot loader did not load. Reload this page to retry.';
            content.append(add(el('div', 'pool-empty'), el('h3', '', 'Pool display temporarily unavailable'), link('Open the aggregate snapshot →', 'pool-observations.json')));
            return {refresh: () => false, stop() {}, getSnapshot: () => null};
        }
        const polling = live.startPolling({load: () => Promise.resolve().then(() => (options.fetchJSON || live.fetchJSON)(options.url || 'pool-observations.json')).then(validateSnapshot), onData: render, onStatus(result) {
            host.setAttribute('aria-busy', 'false');refresh.disabled = false;
            if (!result.ok) {
                status.textContent = `Snapshot check failed (${result.error}). ${lastData ? 'Keeping the last good observations.' : 'The pilot has not published a usable snapshot here yet.'} Retrying every 60 seconds.`;
                if (!lastData) content.replaceChildren(add(el('div', 'pool-empty'), el('h3', '', 'Waiting for the observation pilot'), el('p', '', 'No usable pool snapshot is available. This is not evidence of an empty pool, zero confirmations, or a stopped network.'), el('p', '', 'Use Refresh now to retry. The research checklist below describes the collection and evaluation work.')));
                return;
            }
            const age = live.freshness(lastData?.last_success_at, Date.now(), lastData?.limits?.interval_seconds || 120);
            status.textContent = `${lastData?.state === 'error' || lastData?.error ? 'Observer reports an error' : lastData?.state === 'paused' ? 'Observer paused' : lastData?.state === 'warming_up' ? 'Pilot warming up' : 'Snapshot loaded'} · ${age.state}${age.seconds === null ? '' : ` (${duration(age.seconds)} since last success)`} · page checks every 60 seconds.`;
        }});
        const archivePolling = mountArchive(host, live, options.fetchJSON || live.fetchJSON);
        refresh.addEventListener('click', () => {refresh.disabled = true;Promise.allSettled([polling.check(), archivePolling.check()]).finally(() => {refresh.disabled = false;});});
        return {refresh: () => Promise.allSettled([polling.check(), archivePolling.check()]), stop: () => {polling.stop();archivePolling.stop();}, getSnapshot: () => lastData};
    }
    return {integer, formatInteger, formatXMR, feeDensity, duration, sourceSummary, pollKind, delaySummary, validateSnapshot, derive, archiveSummary, mount};
});
