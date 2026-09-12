(function (root) {
    'use strict';

    const array = value => Array.isArray(value) ? value : [];
    const finite = value => typeof value === 'number' && Number.isFinite(value);
    const number = value => finite(value) ? value.toLocaleString(undefined, {maximumFractionDigits: 5}) : 'Unknown';
    const precise = value => !finite(value) ? 'Unknown' : value !== 0 && Math.abs(value) < .0001 ? value.toExponential(3) : number(value);
    const title = value => String(value || 'Unknown').replaceAll('_', ' ');
    const SOURCE = 'https://github.com/netzo92/deanonymizing_xmr/blob/main/';

    function quality(feature) {
        const keys = ['candidate_count', 'finite_count', 'missing_count', 'nonfinite_count', 'nonnumeric_count'];
        if (keys.some(key => !Number.isSafeInteger(feature[key]) || feature[key] < 0)) return 'unavailable';
        const invalid = feature.missing_count + feature.nonfinite_count + feature.nonnumeric_count;
        if (feature.finite_count + invalid !== feature.candidate_count || feature.finite_count === 0) return 'unavailable';
        if (invalid > 0) return 'incomplete';
        if (!Number.isSafeInteger(feature.distinct_finite) || feature.distinct_finite < 1 || feature.distinct_finite > feature.finite_count) return 'unavailable';
        if (feature.distinct_finite === 1 && feature.globally_constant === true) return 'constant';
        return feature.distinct_finite > 1 && feature.globally_constant === false ? 'varying' : 'unavailable';
    }

    function withinRate(feature) {
        const value = feature?.within_ring;
        if (!value || !Number.isSafeInteger(value.eligible_rings) || value.eligible_rings <= 0 ||
            !Number.isSafeInteger(value.varied_rings) || value.varied_rings < 0 || value.varied_rings > value.eligible_rings) return null;
        return value.varied_rings / value.eligible_rings;
    }

    function selectFeatures(features, query = '', state = 'all') {
        const search = String(query).trim().toLowerCase().replaceAll('_', ' ');
        return array(features).filter(feature => title(feature.name).toLowerCase().includes(search) &&
            (state === 'all' || quality(feature) === state));
    }

    function cohortLabel(cohort) {
        const status = {deterministic_training: 'Training / original candidates', unresolved_scoring: 'Scoring / survivors'}[cohort.status] || title(cohort.status);
        return `${status} · ${title(cohort.amount_branch)} · original size ${cohort.ring_size_bucket ?? 'unknown'}`;
    }

    function validate(data) {
        if (data?.schema_version !== 1 || data?.experiment_id !== 'EA1' || !Array.isArray(data.features) || !data.features.length) {
            throw Error('This feature audit is missing or uses an unsupported schema.');
        }
        const names = data.features.map(feature => feature?.name);
        if (names.some(name => typeof name !== 'string' || !name.length) || new Set(names).size !== names.length) {
            throw Error('Feature names must be present and unique.');
        }
        return data;
    }

    function csvCell(value) {
        let text = value == null ? '' : String(value);
        if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
        return `"${text.replaceAll('"', '""')}"`;
    }

    function csv(data, cohort) {
        const features = cohort ? cohort.features : data.features;
        const label = cohort ? cohortLabel(cohort) : 'All sampled cohorts; candidate-weighted';
        const rows = [['scope', label], ['experiment', data.experiment_id], ['feature_version', data.provenance?.feature_version],
            ['scan_start', data.scope?.scan_start], ['scan_end', data.scope?.scan_end],
            ['feature', 'candidate_count', 'finite_count', 'missing_count', 'nonfinite_count', 'nonnumeric_count',
                'distinct_finite', 'population_variance', 'within_ring_varied', 'within_ring_eligible']];
        for (const feature of array(features)) rows.push([feature.name, feature.candidate_count, feature.finite_count,
            feature.missing_count, feature.nonfinite_count, feature.nonnumeric_count, feature.distinct_finite,
            feature.population_variance, feature.within_ring?.varied_rings, feature.within_ring?.eligible_rings]);
        return rows.map(row => row.map(csvCell).join(',')).join('\r\n');
    }

    function render(host, data) {
        validate(data);
        const el = (tag, text, cls) => {
            const node = document.createElement(tag);
            if (text != null) node.textContent = text;
            if (cls) node.className = cls;
            return node;
        };
        const append = (parent, ...children) => { parent.append(...children); return parent; };
        const link = (label, href) => { const node = el('a', label); node.href = href; return node; };
        const button = label => { const node = el('button', label); node.type = 'button'; return node; };
        const download = (value, type, name) => {
            const url = URL.createObjectURL(new Blob([value], {type}));
            const anchor = link('', url); anchor.download = name; anchor.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
        let selected = data.features[0].name;
        const shell = el('div', null, 'fa-shell');
        shell.append(el('div', 'Measured experiment · EA1', 'fa-kicker'), el('h2', 'Feature observatory'),
            el('p', 'Inspect what varies across candidates, what repeats, and where evidence is missing.', 'hint'));
        const metrics = el('div', null, 'fa-metrics');
        for (const [value, label] of [[data.sampling?.sampled_rings, 'sampled rings'],
            [data.sampling?.sampled_candidates, 'candidate vectors'], [data.features.length, 'features'],
            [data.features.filter(feature => quality(feature) === 'constant').length, 'complete constant columns']]) {
            metrics.append(append(el('div'), el('strong', number(value)), el('span', label)));
        }
        shell.append(el('p', 'Whole-audit totals · all sampled cohorts', 'hint'), metrics);
        const scope = el('p', null, 'fa-scope');
        scope.textContent = `Frozen source: heights ${number(data.scope?.scan_start)}–${number(data.scope?.scan_end)} · last block ${data.scope?.chain_data_at || 'unknown'} · feature version ${data.provenance?.feature_version || 'unknown'}. This experiment is separate from the live collector snapshot.`;
        shell.append(scope);
        const controls = el('div', null, 'fa-controls');
        const cohorts = el('select', null, 'fa-cohort'); cohorts.setAttribute('aria-label', 'Feature audit cohort');
        const all = el('option', 'All sampled cohorts'); all.value = ''; cohorts.append(all);
        array(data.cohorts).forEach(cohort => { const option = el('option', cohortLabel(cohort)); option.value = cohort.key; cohorts.append(option); });
        const query = el('input'); query.type = 'search'; query.placeholder = 'Find a feature…'; query.setAttribute('aria-label', 'Find an audited feature');
        const status = el('select', null, 'fa-status'); status.setAttribute('aria-label', 'Feature variation filter');
        for (const [value, label] of [['all', 'All features'], ['constant', 'Constant'], ['varying', 'Varying'], ['incomplete', 'Incomplete'], ['unavailable', 'Unavailable']]) {
            const option = el('option', label); option.value = value; status.append(option);
        }
        controls.append(append(el('label', 'Cohort'), cohorts), append(el('label', 'Feature'), query), append(el('label', 'Variation'), status));
        const exportButton = button('Cohort CSV'); controls.append(exportButton);
        shell.append(controls);
        const description = el('p', null, 'hint');
        const live = el('p', null, 'fa-live'); live.setAttribute('role', 'status');
        shell.append(description, live);
        const body = el('div', null, 'fa-body'), tableHost = el('div', null, 'fa-table-wrap');
        tableHost.tabIndex = 0; tableHost.setAttribute('role', 'region'); tableHost.setAttribute('aria-label', 'Scrollable feature variation table');
        const detail = el('article', null, 'fa-detail'); detail.tabIndex = -1; detail.setAttribute('aria-label', 'Selected feature details');
        body.append(tableHost, detail); shell.append(body);
        const warnings = el('details'); warnings.append(el('summary', 'Method, provenance, and interpretation'));
        warnings.append(el('p', 'Population variance describes finite candidate values, not prediction quality. Global summaries weight candidates equally; stratified samples are not population estimates. A feature constant within a ring can still help a model through interactions with other features.'));
        for (const limitation of array(data.scope?.limitations)) warnings.append(el('p', String(limitation)));
        for (const [name, value] of Object.entries(data.scope?.membership_scope_by_status || {})) warnings.append(el('p', `${title(name)}: ${typeof value === 'string' ? value : JSON.stringify(value)}`));
        warnings.append(el('p', `Audit source: ${data.provenance?.source_revision || 'unknown'} · scorer SHA-256: ${data.provenance?.scorer_sha256 || 'unknown'} · frozen matrix SHA-256: ${data.matrix?.sha256 || 'unknown'}.`));
        warnings.append(link('Read the experiment and next steps ↗', `${SOURCE}brain/research/feature-variation-audit.md`));
        shell.append(warnings);
        const links = el('p', null, 'fa-links');
        links.append(link('Aggregate JSON', 'feature-audit.json'), document.createTextNode(' · '), link('Experiment source', `${SOURCE}research/feature_variation_audit.py`));
        if (typeof data.matrix?.path === 'string' && /^research\/results\/[a-zA-Z0-9_.-]+\.json\.gz$/.test(data.matrix.path)) {
            links.append(document.createTextNode(' · '), link('Frozen matrix (.gz)', `${SOURCE}${data.matrix.path}`));
        }
        shell.append(links); host.replaceChildren(shell);

        function current() { return array(data.cohorts).find(cohort => cohort.key === cohorts.value); }
        function features() { return current()?.features || data.features; }
        function renderDetail() {
            detail.replaceChildren();
            const feature = array(features()).find(item => item.name === selected);
            if (!feature) { detail.append(el('p', 'Select a feature to compare its cohorts.')); return; }
            detail.append(el('div', 'Selected feature', 'fa-kicker'), el('h3', title(feature.name)), el('code', feature.name));
            const facts = el('dl');
            for (const [label, value] of [['Finite values', `${number(feature.finite_count)} / ${number(feature.candidate_count)}`],
                ['Distinct finite values', number(feature.distinct_finite)], ['Minimum / maximum', `${precise(feature.min)} / ${precise(feature.max)}`],
                ['Mean', precise(feature.mean)], ['Population variance', precise(feature.population_variance)],
                ['Missing / nonfinite / nonnumeric', `${number(feature.missing_count)} / ${number(feature.nonfinite_count)} / ${number(feature.nonnumeric_count)}`],
                ['Incomplete rings', number(feature.within_ring?.incomplete_rings)]]) facts.append(el('dt', label), el('dd', value));
            detail.append(facts, el('h4', 'Within-ring variation by cohort'));
            for (const cohort of array(data.cohorts)) {
                const value = array(cohort.features).find(item => item.name === selected);
                if (!value) continue;
                const rate = withinRate(value), card = el('div', null, 'fa-comparison');
                card.append(el('strong', cohortLabel(cohort)), el('span', rate == null ? 'No eligible rings' : `${number(value.within_ring.varied_rings)} / ${number(value.within_ring.eligible_rings)} eligible rings vary (${number(rate * 100)}%)`));
                const track = el('div', null, 'fa-track'), fill = el('span'); fill.style.width = `${rate == null ? 0 : rate * 100}%`; track.append(fill); card.append(track); detail.append(card);
            }
            const pairs = array(current()?.duplicate_feature_pairs || data.duplicate_feature_pairs).filter(pair => array(pair).includes(selected));
            if (pairs.length) detail.append(el('p', `Exact duplicate columns in this selection: ${pairs.map(pair => pair.filter(name => name !== selected).join(', ')).join('; ')}. Equality in this sample does not establish equivalence in every protocol era.`, 'fa-duplicate'));
        }

        function refresh() {
            const cohort = current(), visible = selectFeatures(features(), query.value, status.value);
            if (!visible.some(feature => feature.name === selected)) selected = visible[0]?.name || null;
            const context = cohort ? data.scope?.membership_scope_by_status?.[cohort.status] : null;
            description.textContent = cohort ? `${number(cohort.sampled_rings)} sampled rings from a frame of ${number(cohort.frame_rings)}; ${number(cohort.sampled_candidates)} candidate vectors. ${typeof context === 'string' ? context : 'Candidate context is unknown.'}` : 'All sampled cohorts combined. Training-style original rings and inference-style reduced rings are separate cohorts below; inspect them separately before interpreting differences.';
            live.textContent = `${visible.length} / ${array(features()).length} features shown`;
            tableHost.replaceChildren();
            const table = el('table'), head = el('thead'), heading = el('tr');
            for (const name of ['Feature', 'State', 'Distinct', 'Variance', 'Rings with variation']) { const th = el('th', name); th.scope = 'col'; heading.append(th); }
            head.append(heading); table.append(head); const rows = el('tbody');
            for (const feature of visible) {
                const tr = el('tr'), name = button(title(feature.name)); name.className = 'fa-feature'; name.setAttribute('aria-pressed', String(selected === feature.name));
                name.addEventListener('click', () => { selected = feature.name; refresh(); detail.focus({preventScroll: true}); detail.scrollIntoView({block: 'nearest'}); });
                const state = quality(feature), badge = el('span', title(state), `fa-state fa-${state}`), rate = withinRate(feature);
                tr.append(append(el('td'), name), append(el('td'), badge), el('td', number(feature.distinct_finite)), el('td', precise(feature.population_variance)),
                    el('td', rate == null ? 'Unknown' : `${number(feature.within_ring.varied_rings)} / ${number(feature.within_ring.eligible_rings)}`));
                rows.append(tr);
            }
            if (!visible.length) { const cell = el('td', 'No features match these filters.'); cell.colSpan = 5; rows.append(append(el('tr'), cell)); }
            table.append(rows); tableHost.append(table); renderDetail();
        }
        query.addEventListener('input', refresh); status.addEventListener('change', refresh); cohorts.addEventListener('change', refresh);
        exportButton.addEventListener('click', () => download(csv(data, current()), 'text/csv;charset=utf-8', 'tracegrove-feature-cohort.csv'));
        refresh();
        return {data};
    }

    async function mount(host, {url = 'feature-audit.json'} = {}) {
        const restoreFocus = host.contains(document.activeElement);
        host.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(url, {cache: 'no-store'});
            if (!response.ok) throw Error(`Feature audit request failed (${response.status}).`);
            const result = render(host, await response.json());
            if (restoreFocus) host.querySelector('.fa-cohort')?.focus({preventScroll: true});
            return result;
        } catch (error) {
            host.replaceChildren();
            const message = document.createElement('p'); message.className = 'notice'; message.textContent = `Feature observatory unavailable: ${error.message}`;
            const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry feature audit'; retry.addEventListener('click', () => mount(host, {url}));
            host.append(message, retry);
            if (restoreFocus) retry.focus({preventScroll: true});
        } finally { host.setAttribute('aria-busy', 'false'); }
    }
    const api = {quality, withinRate, selectFeatures, cohortLabel, validate, csv, render, mount};
    root.TraceGroveFeatureAudit = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
