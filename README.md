# TraceGrove

TraceGrove is a Monero ring-signature research tool. It scans blocks from a Monero node, stores ring data in SQLite, runs deterministic intersection/cascade analysis, and can train ML predictors for unresolved rings.

This is research code. Deterministic resolutions depend on historical ring-size and cascade assumptions; ML predictions are probabilistic and should be treated as hypotheses until forward-verified.

## Repository knowledge

Start at the [Markdown brain](brain/index.md) for a linked hierarchy of
architecture, research context, and development workflows. Each topic includes
source links; [maintenance rules](brain/workflows/maintenance.md) describe how to
keep it current. The database evidence API is documented under **Evidence brain**
below.

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

The schema lives in `models.py` and includes `blocks`, `transactions`, `ring_members`,
`resolved_spends`, `ml_predictions`, `resolution_events`, and `resolution_dependencies`.

## Usage

### Upstream source reference

A pinned Monero source checkout is available locally in `references/monero`.
See the [reference guide](references/README.md) and [heuristic audit](brain/research/upstream-heuristics.md)
for exact source links, findings and proposed experiments. The reference checkout
is ignored by this repository and excluded from deployment archives.

### GCP collector and hosting

See the [GCP deployment guide](deploy/gcp/README.md) for a persistent VM that
collects bounded batches, verifies predictions, periodically scores, and serves
the interactive dashboard through nginx. Deployment uses an explicit committed
release and can seed from a consistent backup of the existing SQLite database.
The collector queries a Monero RPC endpoint; it does not install a full Monero
daemon. Operational behavior and limits are recorded in the
[cloud workflow](brain/workflows/cloud.md).

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

The [TraceGrove dashboard on GCP](http://35.254.148.94/) receives new snapshots
from the persistent collector. [GitHub Pages](https://netzo92.github.io/deanonymizing_xmr/)
serves the committed snapshot. The intended domain is `tracegrove.io`; domain
registration, DNS setup, and HTTPS on the GCP endpoint are pending. See the
[cloud workflow](brain/workflows/cloud.md) for deployment details and collection status.

The historical analysis collector is still catching up. A separate bounded observer
polls recent confirmed blocks every 120 seconds; it measures transaction/ring
activity without resolving modern rings. Browser refresh preserves the last valid
snapshot during failures. The GCP page receives live exports; GitHub Pages remains
a committed mirror.

Export the GitHub Pages dashboard data:

```bash
venv/bin/python main.py export-viz
```

This writes `docs/data.json`, which is rendered by `docs/index.html`. The current dashboard shows:

- resolution summary
- effective ring-size distribution split into fully resolved, partially reduced, and unreduced rings
- a dataset-scoped resolution timeline, with singleton/multi-member breakdowns in new exports
- direct transaction/shared-output groupings with explicit ownership limits
- a current-chain observation feed and automatic 60-second page checks
- a dedicated [TODO/results page](https://netzo92.github.io/deanonymizing_xmr/todos.html) with theoretical implications and real frozen measurements
- historical scan snapshots
- ML prediction verification counts
- ML holdout accuracy and feature importances when exported with `--include-ml-training`
- separate deterministic/hypothesis counts and conflict flags
- a searchable prediction-history table and current evidence inspector
- an interactive Markdown knowledge map, note reader, and TODO browser
- a threshold/cohort analytics lab, uncertainty intervals, and aggregate downloads
- a typed local evidence graph with optional hypothesis edges
- a feature observatory with frozen cohort diagnostics, duplicate columns, and CSV downloads

After editing research notes or TODOs, rebuild their public snapshot:

```bash
python3 brain_export.py
python3 brain_export.py --check
```

The [visual workspace guide](brain/workflows/research-workspace.md) explains the
tools, their evidence limits, and the browser checks required after publication.

Export a bounded selection of historical scores and evidence, then serve locally:

```bash
venv/bin/python main.py export-viz --prediction-limit 200 --evidence-trace-limit 50
venv/bin/python -m http.server 8000 --directory docs --bind 127.0.0.1
```

Open `http://127.0.0.1:8000`. Filters apply to the exported selection; the page
shows its coverage. Scores are uncalibrated. Historical candidate scores belong
to their saved run; inspector explanations describe evidence at export time.
Output amounts and indices are decimal strings in schema-v2 exports to preserve
exact identities in JavaScript. Limits are 1,000 prediction records and 200
events per trace; use `--prediction-limit 0` for aggregates only.

New scoring runs preserve previous predictions, candidate alternatives (including
below-threshold scores), features, model/scaler artifacts, and provenance. Normal
database opening imports old predictions once, preserving known timestamps and
outcomes and labeling missing historical context unknown. Verification checks
outstanding accepted records individually; summary verification counts continue
to describe the latest accepted prediction per ring.

Current dashboard snapshot:

```text
Blocks scanned:    58,901
Total rings:       626,416
Fully resolved:    561,820
Partially reduced: 54,996
Unreduced:         9,600
Resolution rate:   89.69%
Cascade passes:    5
```

Current ML prediction verification:

```text
Total predictions:  15,328
Verified so far:    734
Correct:            591
Wrong:              143
Forward accuracy:   80.5%
Still unverified:   14,594
```

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

### Evidence brain

`brain.py` exposes the existing database as a graph: rings and outputs are nodes,
and ring membership connects them. Outputs are identified by both amount and
index. Each ring memory includes its transaction inputs, stored resolution, and
ML prediction with confidence and verification status.

Inspect graph counts or a particular ring:

```bash
venv/bin/python main.py brain
venv/bin/python main.py brain --key-image <key_image> --related-limit 10 --trace-limit 100
```

The command emits JSON and opens an existing database read-only. It does not run
analysis or load the entire graph into memory. A ring explanation lists original
candidates, the other rings whose stored deterministic resolutions eliminate
them, remaining candidates, and conflicting records. Related rings are ranked by
the number of outputs they share.

Use the typed, immutable records from Python (3.10+):

```python
from brain import Brain
from models import Database

db = Database()
try:
    brain = Brain(db.conn)
    memory = brain.recall(key_image)
    explanation = brain.explain(key_image)
    neighbors = brain.related_rings(key_image, limit=10)
    lineage = brain.trace(key_image, max_nodes=100)
finally:
    db.close()
```

Memories are fetched on demand; recall again after scanning or analysis to see
updates. Predictions and soft-cascade resolutions remain hypotheses and never
eliminate candidates here. Deterministic classification uses the stored pass and
confidence metadata, under the analyzer's existing assumptions. `explain()` shows
current supporting records; `trace()` follows the events recorded when a
resolution was made.

New analysis runs record immutable resolution events with a method, confidence,
scan height, and UTC recording time. Every cascade event links each eliminated
output to the source event that actually removed it. These links retain the
original source claim even if that ring's current resolution later changes.
Repeated runs reuse unchanged events. ML-driven cascades retain hypothesis
status through later passes and cannot become deterministic verification labels.

The `lineage` field in the CLI output contains the root event and its ancestors.
`complete` means all dependency events are available with known methods; it does
not mean the conclusion is deterministic or independently proven. `truncated`
indicates the trace limit was reached, and `missing_event_ids` identifies broken
links. Stored ML events preserve the chosen output and confidence, not a
replayable model snapshot.

Opening the database through a regular command creates the new tables. The next
analysis run imports existing claims as `legacy` events whose original reasoning
is unknown. Their recording time is the import time, not the historical discovery
time, and traces through them remain incomplete. The brain command can still
inspect databases that have not been migrated. Start recording new reasoning with:

```bash
venv/bin/python main.py analyze
venv/bin/python main.py brain --key-image <key_image>
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
Holdout validation: 946/1114 rings correct (84.9%)
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
