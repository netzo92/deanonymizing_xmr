(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.TraceGroveAnalytics = api;
}(typeof globalThis === 'object' ? globalThis : this, function () {
    'use strict';

    const array = value => Array.isArray(value) ? value : [];
    const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    const number = value => typeof value === 'number' && Number.isFinite(value);
    const validScore = value => number(value) && value >= 0 && value <= 1;
    const integer = value => Number.isSafeInteger(value) && value >= 0;
    const present = value => typeof value === 'string' && value.trim().length > 0;
    const yes = value => value === true || value === 1;
    const no = value => value === false || value === 0;
    const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value);
    const rowsOf = data => array(data?.prediction_browser?.rows).filter(object);
    const runsOf = data => new Map(array(data?.prediction_browser?.runs).filter(object)
        .filter(run => present(run.run_id)).map(run => [run.run_id, run]));
    const ratio = (part, total) => total > 0 ? part / total : null;
    const formatCount = value => integer(value) ? value.toLocaleString() : 'Unknown';
    const formatPercent = value => number(value) ? `${(100 * value).toFixed(1)}%` : 'N/A';
    const formatScore = value => validScore(value) ? value.toFixed(3) : 'Unknown';

    function outcome(row) {
        if (no(row.verified)) return 'pending';
        if (yes(row.verified) && yes(row.correct)) return 'correct';
        if (yes(row.verified) && no(row.correct)) return 'wrong';
        return 'unknown';
    }

    function wilson(correct, verified) {
        if (!integer(correct) || !integer(verified) || verified === 0 || correct > verified) return null;
        const z = 1.959963984540054;
        const p = correct / verified;
        const denominator = 1 + z * z / verified;
        const center = (p + z * z / (2 * verified)) / denominator;
        const width = z * Math.sqrt(p * (1 - p) / verified + z * z / (4 * verified * verified)) / denominator;
        return { low: Math.max(0, center - width), high: Math.min(1, center + width), level: 0.95 };
    }

    function summarize(rows, denominator = rows.length) {
        const result = { total: rows.length, accepted: 0, belowRunThreshold: 0, acceptanceUnknown: 0,
            correct: 0, wrong: 0, pending: 0, unknown: 0, unknownScores: 0 };
        for (const row of rows) {
            result[outcome(row)]++;
            if (yes(row.accepted)) result.accepted++;
            else if (no(row.accepted)) result.belowRunThreshold++;
            else result.acceptanceUnknown++;
            if (!validScore(row.confidence)) result.unknownScores++;
        }
        result.verified = result.correct + result.wrong;
        result.coverageDenominator = denominator;
        result.coverage = ratio(result.total, denominator);
        result.verifiedFraction = ratio(result.verified, result.total);
        result.accuracy = ratio(result.correct, result.verified);
        result.interval = wilson(result.correct, result.verified);
        return result;
    }

    function cohort(row, runs) {
        const id = present(row.run_id) ? row.run_id : null;
        const run = id ? runs.get(id) : null;
        const legacy = run?.metadata?.origin === 'legacy_import';
        const height = integer(row.scan_height) ? row.scan_height : integer(run?.scan_height) ? run.scan_height : null;
        const origin = legacy ? 'Legacy import' : run ? 'Recorded run' : id ? 'Run metadata missing' : 'Unknown run';
        return { key: JSON.stringify([id, height, origin]), runId: id, scanHeight: height, origin,
            label: `${origin} · ${id || 'ID unknown'} · ${height === null ? 'scan unknown' : `scan ${height}`}` };
    }

    function selectRows(data, options = {}) {
        const threshold = validScore(options.threshold) ? options.threshold : 0.95;
        const rows = rowsOf(data);
        const runs = runsOf(data);
        const includedCohort = options.cohort && options.cohort !== 'all'
            ? rows.filter(row => cohort(row, runs).key === options.cohort) : rows;
        return { rows, runs, includedCohort, threshold,
            selected: includedCohort.filter(row => validScore(row.confidence) && row.confidence >= threshold) };
    }

    function scoreBins(rows, bins = 10) {
        if (!integer(bins) || bins < 1 || bins > 100) throw new RangeError('Bin count must be between 1 and 100');
        const groups = Array.from({ length: bins }, (_, index) => ({
            lower: index / bins, upper: (index + 1) / bins, includesUpper: index === bins - 1, rows: [],
        }));
        let unknown = 0;
        for (const row of rows) {
            if (!validScore(row.confidence)) { unknown++; continue; }
            groups[Math.min(bins - 1, Math.floor(row.confidence * bins))].rows.push(row);
        }
        return { unknown, bins: groups.map(group => {
            const verified = group.rows.filter(row => outcome(row) === 'correct' || outcome(row) === 'wrong');
            return { lower: group.lower, upper: group.upper,
                includesUpper: group.includesUpper, ...summarize(group.rows, rows.length),
                meanScore: group.rows.length ? group.rows.reduce((sum, row) => sum + row.confidence, 0) / group.rows.length : null,
                meanVerifiedScore: verified.length ? verified.reduce((sum, row) => sum + row.confidence, 0) / verified.length : null,
            };
        }) };
    }

    function grouped(rows, keyFor) {
        const groups = new Map();
        for (const row of rows) {
            const key = keyFor(row);
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key).push(row);
        }
        return [...groups].map(([label, members]) => ({ label, ...summarize(members, rows.length) }));
    }

    function verificationCohort(row) {
        if (outcome(row) === 'pending') return 'Pending verification';
        if (!yes(row.verified)) return 'Verification state unknown';
        if (row.verification_provenance === 'legacy_unknown') return 'Legacy verification · provenance/time unknown';
        if (row.verification_provenance !== 'deterministic_resolution') return 'Other/unknown verification provenance';
        if (!integer(row.scan_height) || !integer(row.verification_height)) return 'Deterministic verification · scan timing unknown';
        return row.verification_height > row.scan_height
            ? 'Deterministic verification · later scanned cutoff' : 'Deterministic verification · same/earlier cutoff';
    }

    function candidateAmbiguity(row, run) {
        const candidates = array(row.candidates);
        if (run?.metadata?.origin === 'legacy_import'
                || array(run?.metadata?.unknown).includes('candidate_alternatives')) {
            return { state: 'Legacy alternatives unavailable', margin: null };
        }
        if (yes(row.candidates_truncated) || integer(row.candidates_total) && row.candidates_total > candidates.length) {
            return { state: 'Candidate list truncated', margin: null };
        }
        if (!no(row.candidates_truncated) || !integer(row.candidates_total) || row.candidates_total !== candidates.length) {
            return { state: 'Candidate completeness unknown', margin: null };
        }
        if (candidates.length < 2) return { state: 'Fewer than two stored alternatives', margin: null };
        const identityPart = value => present(value) || integer(value);
        if (candidates.some(item => !object(item) || !validScore(item.score)
                || !identityPart(item.amount) || !identityPart(item.index))) {
            return { state: 'Missing/invalid candidate scores or identities', margin: null };
        }
        const identities = candidates.map(item => JSON.stringify([String(item.amount), String(item.index)]));
        if (new Set(identities).size !== identities.length) return { state: 'Duplicate candidate identities', margin: null };
        const scores = candidates.map(item => item.score).sort((a, b) => b - a);
        return { state: 'Complete scored alternatives', margin: scores[0] - scores[1] };
    }

    function ambiguitySummary(rows, runs) {
        const states = new Map();
        const margins = [
            { label: 'Tied / gap below 0.01', upper: 0.01, count: 0 },
            { label: 'Gap 0.01–below 0.05', upper: 0.05, count: 0 },
            { label: 'Gap 0.05–below 0.20', upper: 0.2, count: 0 },
            { label: 'Gap 0.20–1.00', upper: Infinity, count: 0 },
        ];
        let complete = 0;
        for (const row of rows) {
            const value = candidateAmbiguity(row, runs.get(row.run_id));
            states.set(value.state, (states.get(value.state) || 0) + 1);
            if (value.margin !== null) {
                complete++;
                margins.find(bin => value.margin < bin.upper).count++;
            }
        }
        return { total: rows.length, complete, states: [...states].map(([label, count]) => ({ label, count })),
            margins: margins.map(({ label, count }) => ({ label, count })) };
    }

    function reductionSummary(rows) {
        const rings = new Map();
        const conflictRings = new Set();
        let missingIdentities = 0;
        for (const row of rows) {
            if (!present(row.key_image)) { missingIdentities++; continue; }
            const evidence = row.evidence;
            if (array(evidence?.conflicts).length > 0) conflictRings.add(row.key_image);
            const original = evidence?.candidate_count;
            const remaining = evidence?.remaining_candidate_count;
            const counts = integer(original) && original > 0 && integer(remaining) && remaining <= original
                ? { original, remaining, conflict: array(evidence?.conflicts).length > 0 } : null;
            if (!rings.has(row.key_image)) rings.set(row.key_image, counts);
            else if (JSON.stringify(rings.get(row.key_image)) !== JSON.stringify(counts)) rings.set(row.key_image, null);
        }
        const groups = new Map(['Zero candidates', 'One candidate', 'Reduced, at least two candidates',
            'Unchanged candidate count', 'Unknown/inconsistent counts'].map(label => [label, 0]));
        for (const counts of rings.values()) {
            let label = 'Unknown/inconsistent counts';
            if (counts) {
                label = counts.remaining === 0 ? 'Zero candidates' : counts.remaining === 1 ? 'One candidate'
                    : counts.remaining < counts.original ? 'Reduced, at least two candidates' : 'Unchanged candidate count';
            }
            groups.set(label, groups.get(label) + 1);
        }
        return { uniqueRings: rings.size, missingIdentities, conflicts: conflictRings.size,
            groups: [...groups].map(([label, count]) => ({ label, count })) };
    }

    function provenanceSummary(rows, runs) {
        const verified = rows.filter(row => yes(row.verified));
        const checks = [
            ['Run metadata available', row => object(runs.get(row.run_id)?.metadata)],
            ['Prediction scan height known', row => integer(row.scan_height)],
            ['Feature version recorded', row => present(runs.get(row.run_id)?.metadata?.feature_version)],
            ['Model artifact hash recorded', row => hash(runs.get(row.run_id)?.metadata?.artifact_sha256)],
            ['Working source hash recorded', row => hash(runs.get(row.run_id)?.metadata?.working_source_sha256)],
            ['Complete alternative scores', row => candidateAmbiguity(row, runs.get(row.run_id)).margin !== null],
        ].map(([label, check]) => ({ label, known: rows.filter(check).length, total: rows.length }));
        checks.push({ label: 'Verified rows with deterministic provenance and both scan heights',
            known: verified.filter(row => row.verification_provenance === 'deterministic_resolution'
                && integer(row.scan_height) && integer(row.verification_height)).length, total: verified.length });
        return checks;
    }

    function analyze(data, options = {}) {
        const { rows, runs, selected, includedCohort, threshold } = selectRows(data, options);
        const cohorts = new Map(rows.map(row => { const value = cohort(row, runs); return [value.key, value]; }));
        const total = integer(data?.prediction_browser?.total) ? data.prediction_browser.total : null;
        return {
            scope: { includedRecords: rows.length, historicalRecords: total,
                omittedRecords: total === null || total < rows.length ? null : total - rows.length,
                datasetId: data?.scope?.dataset_id ?? null, exportedAt: data?.scope?.exported_at ?? null,
                ordering: data?.prediction_browser?.order === 'newest_first' ? 'newest first' : 'export order',
                unit: 'historical scoring records, not independent rings',
                limitations: 'Export selection and verification selection can bias these descriptive results. Scores are uncalibrated; Wilson intervals do not correct selection bias or dependence between records.',
            },
            filter: { threshold, cohort: options.cohort || 'all', cohortRecords: includedCohort.length,
                unknownScoresExcluded: includedCohort.filter(row => !validScore(row.confidence)).length },
            cohorts: [...cohorts.values()],
            retained: summarize(selected, rows.length),
            scoreDistribution: scoreBins(includedCohort),
            byRingSize: grouped(selected, row => integer(row.original_ring_size) && row.original_ring_size > 0
                ? String(row.original_ring_size) : 'Unknown').sort((a, b) => a.label === 'Unknown' ? 1 : b.label === 'Unknown' ? -1 : Number(a.label) - Number(b.label)),
            byCohort: grouped(selected, row => cohort(row, runs).label),
            byVerification: grouped(selected, verificationCohort),
            ambiguity: ambiguitySummary(selected, runs),
            reduction: reductionSummary(selected),
            provenance: provenanceSummary(selected, runs),
        };
    }

    function csvCell(value) {
        let text = value === null || value === undefined ? '' : String(value);
        if (typeof value === 'string' && (/^\s*[=+\-@]/.test(text) || /^[\t\r]/.test(text))) text = `'${text}`;
        return `"${text.replace(/"/g, '""')}"`;
    }

    function aggregateCSV(report) {
        const lines = [['section', 'group', 'records', 'correct', 'wrong', 'pending', 'outcome_unknown',
            'verified', 'verified_accuracy', 'wilson95_low', 'wilson95_high', 'denominator', 'mean_score_all', 'mean_score_verified']];
        const add = (section, label, row) => lines.push([section, label, row.total, row.correct, row.wrong,
            row.pending, row.unknown, row.verified, row.accuracy, row.interval?.low, row.interval?.high, row.coverageDenominator,
            row.meanScore, row.meanVerifiedScore]);
        lines.push(['scope', `Threshold ${report.filter.threshold}; included ${report.scope.includedRecords}; historical ${report.scope.historicalRecords ?? 'unknown'}; ${report.scope.limitations}`]);
        lines.push(['scope', `Dataset ${report.scope.datasetId ?? 'unknown'}; exported ${report.scope.exportedAt ?? 'unknown'}; cohort ${report.filter.cohort}`]);
        lines.push(['unknownScoresBeforeThreshold', 'Selected cohort', report.filter.unknownScoresExcluded]);
        add('retained', 'Selected records', report.retained);
        for (const section of ['byRingSize', 'byCohort', 'byVerification']) {
            for (const row of report[section]) add(section, row.label, row);
        }
        for (const row of report.scoreDistribution.bins) add('scoreBinsBeforeThreshold', `${row.lower}–${row.upper}`, row);
        for (const row of report.provenance) lines.push(['provenance', row.label, row.known, '', '', '', '', '', '', '', '', row.total]);
        for (const row of report.ambiguity.states) lines.push(['candidateCompleteness', row.label, row.count]);
        for (const row of report.reduction.groups) lines.push(['currentUniqueRingReduction', row.label, row.count]);
        return lines.map(line => line.map(csvCell).join(',')).join('\r\n') + '\r\n';
    }

    function mount(host, initialData, hooks = {}) {
        if (!host || typeof host.replaceChildren !== 'function') throw new TypeError('An analytics host element is required');
        const document = host.ownerDocument;
        let data = initialData;
        let options = { threshold: 0.95, cohort: 'all' };
        let report;
        let destroyed = false;
        const node = (tag, className, text) => {
            const value = document.createElement(tag);
            if (className) value.className = className;
            if (text !== undefined) value.textContent = String(text);
            return value;
        };
        const append = (parent, ...children) => { parent.append(...children); return parent; };
        const section = node('section', 'tg-analytics');
        section.setAttribute('aria-label', 'Research analytics');
        append(section, node('p', 'tg-analytics-eyebrow', 'EXPLORE THE EVIDENCE'), node('h2', '', 'Research analytics'),
            node('p', 'tg-analytics-intro', 'Change a score cutoff, compare outcomes, and see where the exported evidence runs out.'));
        const scope = node('p', 'tg-analytics-notice');
        section.append(scope);
        const controls = node('div', 'tg-analytics-controls');
        const thresholdLabel = node('label', '', 'Minimum model score');
        const threshold = node('input');
        threshold.type = 'range'; threshold.min = '0'; threshold.max = '1'; threshold.step = '0.01'; threshold.value = '0.95';
        const thresholdValue = node('output', 'tg-analytics-threshold', '0.95');
        append(thresholdLabel, threshold, thresholdValue);
        const cohortLabel = node('label', '', 'Scoring cohort');
        const cohortSelect = node('select');
        cohortLabel.append(cohortSelect);
        const downloads = node('div', 'tg-analytics-downloads');
        const csvButton = node('button', '', 'Aggregate CSV');
        const jsonButton = node('button', '', 'Aggregate JSON');
        csvButton.type = jsonButton.type = 'button';
        append(downloads, csvButton, jsonButton);
        append(controls, thresholdLabel, cohortLabel, downloads);
        section.append(controls);
        const live = node('p', 'tg-analytics-live');
        live.setAttribute('role', 'status'); live.setAttribute('aria-live', 'polite');
        section.append(live);
        const content = node('div');
        section.append(content);
        host.replaceChildren(section);

        function table(parent, title, headers, rows) {
            const wrap = node('div', 'tg-analytics-table-wrap');
            wrap.tabIndex = 0; wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', title);
            const table = node('table');
            table.append(node('caption', '', title));
            const head = node('tr');
            headers.forEach(text => { const cell = node('th', '', text); cell.scope = 'col'; head.append(cell); });
            table.append(append(node('thead'), head));
            const body = node('tbody');
            for (const values of rows) {
                const tr = node('tr');
                values.forEach(value => tr.append(append(node('td'), value?.nodeType ? value : document.createTextNode(String(value ?? 'Unknown')))));
                body.append(tr);
            }
            if (!rows.length) { const cell = node('td', '', 'No records in this selection.'); cell.colSpan = headers.length; body.append(append(node('tr'), cell)); }
            append(parent, append(wrap, append(table, body)));
        }

        function panel(parent, title, subtitle) {
            const box = node('section', 'tg-analytics-panel');
            append(box, node('h3', '', title));
            if (subtitle) box.append(node('p', 'tg-analytics-note', subtitle));
            parent.append(box);
            return box;
        }

        function bars(parent, values, denominator) {
            const chart = node('div', 'tg-analytics-bars');
            for (const value of values) {
                const row = node('div', 'tg-analytics-bar-row');
                const track = node('div', 'tg-analytics-track'); track.setAttribute('aria-hidden', 'true');
                const fill = node('span'); fill.style.width = `${denominator > 0 ? Math.min(100, 100 * value.count / denominator) : 0}%`;
                track.append(fill);
                append(row, node('span', '', value.label), track, node('strong', '', formatCount(value.count)));
                chart.append(row);
            }
            parent.append(chart);
        }

        function outcomeTable(parent, title, values, firstHeader) {
            table(parent, title, [firstHeader, 'Records', 'Correct', 'Wrong', 'Pending', 'Unknown', 'Verified accuracy'],
                values.map(value => [value.label, value.total, value.correct, value.wrong, value.pending, value.unknown, formatPercent(value.accuracy)]));
        }

        function refresh() {
            if (destroyed) return;
            report = analyze(data, options);
            const r = report.retained;
            scope.textContent = `${formatCount(report.scope.includedRecords)} included of ${formatCount(report.scope.historicalRecords)} historical records (${report.scope.ordering}). All tools operate on this exported subset; repeated predictions of the same ring count as separate records.`;
            thresholdValue.textContent = options.threshold.toFixed(2);
            threshold.setAttribute('aria-valuetext', `Model score at least ${options.threshold.toFixed(2)}`);
            live.textContent = `${r.total} retained · ${formatPercent(r.coverage)} of all ${report.scope.includedRecords} included records · ${report.filter.unknownScoresExcluded} unknown/invalid scores excluded in this cohort.`;
            content.replaceChildren();
            const metrics = node('div', 'tg-analytics-metrics');
            const metricData = [
                ['Retained records', formatCount(r.total), `${formatPercent(r.coverage)} of all included records`],
                ['Accepted in saved run', formatCount(r.accepted), `${r.belowRunThreshold} below run threshold; ${r.acceptanceUnknown} unknown`],
                ['Pending verification', formatCount(r.pending), `${r.unknown} outcomes unknown`],
                ['Verified outcomes', `${r.correct} correct / ${r.wrong} wrong`, `${formatPercent(r.verifiedFraction)} of retained records verified`],
                ['Verified-only accuracy', formatPercent(r.accuracy), r.interval
                    ? `95% Wilson interval ${formatPercent(r.interval.low)}–${formatPercent(r.interval.high)}; n=${r.verified}` : 'No observed correct/wrong outcomes'],
            ];
            for (const [label, value, hint] of metricData) append(metrics,
                append(node('div', 'tg-analytics-metric'), node('span', '', label), node('strong', '', value), node('small', '', hint)));
            content.append(metrics);
            content.append(node('p', 'tg-analytics-note', 'Moving the cutoff simulates retention and does not change saved acceptance decisions. Accuracy uses correct / (correct + wrong). Pending and unknown outcomes are excluded. The verified subset is selective; the Wilson interval assumes independent binomial outcomes and does not correct selection bias, repeated rings, or model dependence.'));
            const grid = node('div', 'tg-analytics-grid');
            content.append(grid);
            const reliability = panel(grid, 'Score distribution & observed outcomes',
                'Selected cohort before the cutoff. Mean score (all) includes pending and unknown outcomes; mean score (verified), accuracy, and intervals use only observed correct/wrong outcomes. This is a descriptive audit of uncalibrated scores, not validated calibration or a probability forecast.');
            const bins = report.scoreDistribution.bins;
            bars(reliability, bins.map(bin => ({ label: `${bin.lower.toFixed(1)}–${bin.upper.toFixed(1)}`, count: bin.total })),
                Math.max(0, ...bins.map(bin => bin.total)));
            const binDetails = append(node('details'), node('summary', '', 'Inspect bin counts and intervals'));
            table(binDetails, 'Score bins; left edge included, right edge excluded except 1.0',
                ['Score bin', 'Records', 'Mean score (all)', 'Mean score (verified)', 'Verified n', 'Correct', 'Wrong', 'Pending', 'Unknown', 'Accuracy (verified)', '95% Wilson'],
                bins.map(bin => [`${bin.lower.toFixed(1)}–${bin.upper.toFixed(1)}${bin.includesUpper ? ' inclusive' : ''}`,
                    bin.total, formatScore(bin.meanScore), formatScore(bin.meanVerifiedScore), bin.verified, bin.correct, bin.wrong, bin.pending, bin.unknown,
                    formatPercent(bin.accuracy), bin.interval ? `${formatPercent(bin.interval.low)}–${formatPercent(bin.interval.high)}` : 'N/A']));
            reliability.append(binDetails);
            reliability.append(node('p', 'tg-analytics-note', `${report.scoreDistribution.unknown} records have unknown/invalid scores and are outside the bins.`));

            const ambiguity = panel(grid, 'Candidate ambiguity',
                'The gap is the largest minus the second-largest stored candidate score. It measures ranking separation, not certainty. Only complete scored alternative lists enter the gap distribution.');
            bars(ambiguity, report.ambiguity.margins, report.ambiguity.complete);
            table(ambiguity, 'Candidate-list coverage in retained records', ['Availability', 'Records'],
                report.ambiguity.states.map(value => [value.label, value.count]));
            ambiguity.append(node('p', 'tg-analytics-note', 'Legacy selected-output-only records cannot establish a runner-up gap. Truncated or otherwise incomplete alternatives remain unknown. Stored alternatives may already reflect earlier eliminations.'));
            if (typeof hooks.onInspect === 'function') {
                const { selected, runs } = selectRows(data, options);
                const examples = selected.map(row => ({ row, ...candidateAmbiguity(row, runs.get(row.run_id)) }))
                    .filter(value => value.margin !== null).sort((a, b) => a.margin - b.margin).slice(0, 5);
                if (examples.length) table(ambiguity, 'Smallest complete score gaps', ['Record', 'Gap', 'Evidence'], examples.map(value => {
                    const button = node('button', '', 'Inspect'); button.type = 'button';
                    button.setAttribute('aria-label', `Inspect prediction ${value.row.prediction_id ?? 'record'}`);
                    button.addEventListener('click', () => hooks.onInspect(value.row));
                    return [String(value.row.prediction_id ?? 'Unknown ID'), formatScore(value.margin), button];
                }));
            }

            const reduction = panel(grid, 'Current candidate reduction',
                'Current exported evidence, deduplicated by key image across retained records. Full count fields remain usable when the displayed candidate arrays are truncated. This view is separate from scoring-time alternatives.');
            bars(reduction, report.reduction.groups, report.reduction.uniqueRings);
            reduction.append(node('p', 'tg-analytics-note', `${report.reduction.uniqueRings} unique rings; ${report.reduction.missingIdentities} records lack a usable ring identity. ${report.reduction.conflicts} rings have conflict flags (overlapping diagnostic count). One remaining candidate does not independently validate a resolution.`));

            const provenance = panel(grid, 'Provenance completeness',
                'Availability among retained records. A recorded artifact hash indicates a reference; the model artifact itself is not part of this dashboard export.');
            table(provenance, 'Known metadata and explicit denominators', ['Field', 'Known / eligible', 'Coverage'],
                report.provenance.map(value => [value.label, `${value.known} / ${value.total}`, formatPercent(ratio(value.known, value.total))]));

            const cohorts = panel(content, 'Outcome comparisons',
                'Retained scoring records. Ring size is the exported original membership count. Legacy or missing run/scan context stays explicit; later scanned cutoffs do not prove that labels were unavailable at prediction time.');
            outcomeTable(cohorts, 'Outcomes by original ring size', report.byRingSize, 'Ring size');
            const cohortDetails = append(node('details'), node('summary', '', 'Compare scoring and verification cohorts'));
            outcomeTable(cohortDetails, 'Outcomes by scoring cohort', report.byCohort, 'Scoring cohort');
            outcomeTable(cohortDetails, 'Outcomes by verification provenance/timing', report.byVerification, 'Verification cohort');
            cohorts.append(cohortDetails);
        }

        function rebuildCohorts() {
            const values = analyze(data, { threshold: 0 }).cohorts;
            cohortSelect.replaceChildren();
            const all = node('option', '', 'All included runs'); all.value = 'all'; cohortSelect.append(all);
            for (const value of values) { const option = node('option', '', value.label); option.value = value.key; cohortSelect.append(option); }
            if (!values.some(value => value.key === options.cohort)) options.cohort = 'all';
            cohortSelect.value = options.cohort;
        }

        function download(extension) {
            const window = document.defaultView;
            const payload = extension === 'csv' ? aggregateCSV(report) : JSON.stringify(report, null, 2);
            const blob = new window.Blob([payload], { type: extension === 'csv' ? 'text/csv;charset=utf-8' : 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const link = node('a'); link.href = url; link.download = `tracegrove-aggregate-analytics.${extension}`;
            section.append(link); link.click(); link.remove();
            window.setTimeout(() => window.URL.revokeObjectURL(url), 1000);
        }
        threshold.addEventListener('input', () => { options.threshold = Number(threshold.value); refresh(); });
        cohortSelect.addEventListener('change', () => { options.cohort = cohortSelect.value; refresh(); });
        csvButton.addEventListener('click', () => download('csv'));
        jsonButton.addEventListener('click', () => download('json'));
        rebuildCohorts(); refresh();
        return {
            update(nextData) { if (!destroyed) { data = nextData; rebuildCohorts(); refresh(); } },
            destroy() { destroyed = true; section.remove(); },
        };
    }

    return { mount, analyze, selectRows, summarize, outcome, wilson, scoreBins, cohort,
        candidateAmbiguity, reductionSummary, provenanceSummary, aggregateCSV, csvCell };
}));
