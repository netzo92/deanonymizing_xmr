---
summary: Setup, command side effects, and focused validation commands.
status: maintained
reviewed: 2026-09-11
---

# Development

Parent: [workflows](index.md).

Sources: [CLI handlers](../../main.py), [dependencies](../../requirements.txt),
[database initialization](../../models.py). Run commands from the repository root.

## Setup and checks

The source uses Python 3.10+ syntax. Setup:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Run existing synthetic-data tests after changes to analysis, storage, or scoring:

```bash
venv/bin/python -m unittest discover -s tests -v
```

For focused checks, add `-p 'test_brain.py'`, `-p 'test_lineage.py'`, or
`-p 'test_prediction_verification.py'` to the discovery command. These suites
cover graph queries/CLI, dependency provenance, and prediction verification.
They do not establish model quality or successful live-node ingestion.

For dashboard filtering, evidence categories, exact identities, and history
cohorts, run the dependency-free JavaScript checks:

```bash
node --test tests/test_*.js
node --check docs/dashboard.js
python3 brain_export.py --check
```

Prediction-history tests also cover migration, transaction rollback, artifact
storage, and independent historical verification. Export tests cover bounded
evidence, reconciled counts, and keeping the previous JSON file when export fails.

## Command effects

Global options such as `--db` and `--node` go before the subcommand.

| Command | Effects |
| --- | --- |
| `brain` | Reads an existing database; no schema creation or analysis |
| `status` | Reads statistics after regular database initialization, which can create schema |
| `scan` | Fetches node data and writes blocks, transactions, and memberships |
| `analyze` | Runs analysis and writes resolutions and history |
| `predict` | Runs analysis, trains, and saves ML predictions without applying ML guesses to the cascade |
| `analyze --score` | Also applies ML predictions and soft cascades to stored resolutions |
| `verify` | Runs analysis and writes prediction verification results |
| `export`, `diagnose` | Run analysis before exporting or diagnosing |
| `export-viz` | Runs analysis and writes dashboard JSON; optional training |

Dashboard schema v2 includes the newest 200 historical scoring records by default
and bounded current evidence. Export up to 1,000 records, or use zero for aggregate
data only:

```bash
venv/bin/python main.py --db forward_test.db export-viz --prediction-limit 200 --evidence-trace-limit 50
venv/bin/python -m http.server 8000 --directory docs --bind 127.0.0.1
```

Open `http://127.0.0.1:8000` to load the local dashboard. The browser fetches JSON,
so serve the directory over HTTP. Filters apply only to the exported records;
global counts retain their full-dataset denominators. Ordinary database opening
performs the one-time prediction-history migration. No old model/feature metadata
is invented for imported rows.

Inspect existing evidence:

```bash
venv/bin/python main.py --db monero_analysis.db brain
venv/bin/python main.py --db monero_analysis.db brain --key-image <key_image> --trace-limit 100
```

Forward-test sequence (replace the database path and heights):

```bash
venv/bin/python main.py --db forward_test.db --node http://127.0.0.1:18081 scan --end <initial_height>
venv/bin/python main.py --db forward_test.db predict --confidence 0.95
# Later, after additional blocks are available:
venv/bin/python main.py --db forward_test.db --node http://127.0.0.1:18081 scan --end <later_height>
venv/bin/python main.py --db forward_test.db verify
```

The placeholders must be replaced before execution. See
[evaluation](../research/evaluation.md) for dataset isolation and interpretation.
The [README](../../README.md) contains additional usage and dashboard instructions.
