# deanonymizing_xmr

Monero ring-signature analysis tool. It scans blocks from a Monero node, stores ring data in SQLite, runs deterministic intersection/cascade analysis, and can train ML predictors for unresolved rings.

This is research code. Deterministic resolutions depend on historical ring-size and cascade assumptions; ML predictions are probabilistic and should be treated as hypotheses until forward-verified.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Database

Tables are created automatically when any command opens the SQLite database. The default path is `monero_analysis.db`; use `--db <path>` to choose another file.

Create an empty database and schema:

```bash
venv/bin/python main.py status
```

Or create and populate it by scanning blocks:

```bash
venv/bin/python main.py --node http://127.0.0.1:18081 scan --start 0 --end 1000
```

The schema lives in `models.py` and includes `blocks`, `transactions`, `ring_members`, `resolved_spends`, and `ml_predictions`.

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

`predict` trains on deterministic resolutions, scores unresolved rings, and saves high-confidence ML predictions without applying those ML guesses to `resolved_spends`. Use this for clean forward-testing.

`analyze --score` also trains and scores, but then applies high-confidence predictions during soft cascade. Use it for exploratory analysis, not clean forward-testing.

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

Include ML holdout accuracy and feature importances:

```bash
venv/bin/python main.py export-viz --include-ml-training
```

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

Training hygiene:

- labels come only from `resolved_spends` rows with `confidence = 1.0` and non-negative deterministic pass numbers
- train/test splitting is done by ring, so candidates from the same ring cannot appear in both train and holdout
- feature scaling is fit on the train split only, then applied to holdout rings
- after holdout scoring is reported, the model is retrained on all deterministic rings before saving predictions
- saved predictions live in `ml_predictions`; they are not training labels unless a later deterministic run independently resolves them

Important caveat: the holdout split is not a full forward-time test. Some graph-derived features, such as output reuse counts, are computed from the current scanned snapshot. Use the forward verification workflow below for the cleaner test of predictions made before later blocks are scanned.

## Forward verification workflow

Forward verification checks whether predictions made today are later confirmed by deterministic cascade evidence after scanning more blocks.

1. Save high-confidence predictions:

```bash
venv/bin/python main.py predict --confidence 0.95
```

This writes to `ml_predictions` only. It does not write ML guesses into `resolved_spends`, so later verification is not contaminated by the predictions being tested.

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

Caveat: keep `analyze --score` out of clean forward-test runs. It is still useful for exploratory soft-cascade experiments because it intentionally applies ML predictions to the cascade.
