# deanonymizing_xmr

Monero ring-signature analysis tool. It scans blocks from a Monero node, stores ring data in SQLite, runs deterministic intersection/cascade analysis, and can train ML predictors for unresolved rings.

This is research code. Deterministic resolutions depend on historical ring-size and cascade assumptions; ML predictions are probabilistic and should be treated as hypotheses until forward-verified.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

### Scan blocks

Fetch block data from a Monero node into the local database. Use `--node` to specify a public node if you don't have one running locally.

```bash
venv/bin/python main.py --node http://xmr-node.cakewallet.com:18081 scan --start 0 --end 1000 --delay 100
```

### Analyze

Run the intersection attack on scanned data.

```bash
venv/bin/python main.py analyze
```

With ML-based probabilistic scoring and soft cascade:

```bash
venv/bin/python main.py analyze --score --confidence 0.95
```

`analyze --score` trains on deterministic resolutions, scores unresolved rings, saves high-confidence ML predictions, and applies those predictions during soft cascade. Use this for exploratory analysis, not clean forward-testing.

### Export results

```bash
venv/bin/python main.py export --output results.json
```

### Dashboard

Export the GitHub Pages dashboard data:

```bash
venv/bin/python main.py export-viz
```

This writes `docs/data.json`, which is rendered by `docs/index.html`. The current dashboard shows:

- resolution summary
- effective ring-size distribution split into fully resolved, partially reduced, and unreduced rings
- historical scan snapshots
- ML prediction verification counts

To publish the dashboard:

```bash
git add docs/data.json docs/index.html
git commit -m "Update dashboard"
git push origin main
```

### Check status

```bash
venv/bin/python main.py status
```

### Diagnose empty rings

Size-0 rings are analyzer/data-consistency artifacts, not real Monero rings. Diagnose them with:

```bash
venv/bin/python main.py diagnose
```

The analyzer resolves ring-size-1 ground-truth rings before cascade eliminations to avoid producing impossible size-0 rings.

## ML predictor accuracy

The scorer reports ring-level holdout accuracy during training. This is the percentage of held-out deterministically labeled rings where the model's top-scored candidate matches the known real spend.

Example current train-only result:

```text
Holdout validation: 441/518 rings correct (85.1%)
```

This is not the same as forward verification. Holdout is useful for model iteration, but it is still measured on rings that are deterministically resolvable in the current dataset.

## Forward verification workflow

Forward verification checks whether predictions made today are later confirmed by deterministic cascade evidence after scanning more blocks.

1. Save high-confidence predictions:

```bash
venv/bin/python main.py analyze --score --confidence 0.95
```

2. Later, scan more blocks:

```bash
venv/bin/python main.py scan --end <later_block_height>
```

3. Re-run deterministic analysis and verify saved predictions:

```bash
venv/bin/python main.py analyze
venv/bin/python main.py verify
```

4. Check prediction stats:

```bash
venv/bin/python -c "from models import Database; db=Database(); print(db.get_prediction_stats()); db.close()"
```

Caveat: `analyze --score` currently applies ML predictions to the cascade, which can contaminate a clean forward-test setup. A future `predict-only` mode should save predictions without writing them into `resolved_spends`.
