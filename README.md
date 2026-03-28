# deanonymizing_xmr

Monero ring signature intersection attack tool. Scans blocks from a Monero node, stores ring data in SQLite, and performs deterministic + ML-based probabilistic analysis to resolve true spends.

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
python main.py --node http://xmr-node.cakewallet.com:18081 scan --start 0 --end 1000 --delay 100
```

### Analyze

Run the intersection attack on scanned data.

```bash
python main.py analyze
```

With ML-based probabilistic scoring:

```bash
python main.py analyze --score --confidence 0.95
```

### Export results

```bash
python main.py export --output results.json
```

### Check status

```bash
python main.py status
```
