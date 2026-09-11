#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sqlite3
import shlex
import tempfile
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from brain import Brain
from monero_rpc import MoneroRPC
from models import Database
from scanner import Scanner
from analyzer import Analyzer
from scorer import RingScorer
from dashboard_export import evidence_summary, prediction_browser


def setup_logging(verbose):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_scan(args):
    rpc = MoneroRPC(args.node, delay=args.delay)
    db = Database(args.db)
    scanner = Scanner(rpc, db)
    scanner.scan(
        start_height=args.start,
        end_height=args.end,
        batch_commit=args.batch_size,
        log_every=args.log_every,
    )
    db.close()


def cmd_analyze(args):
    db = Database(args.db)
    analyzer = Analyzer(db)
    stats = analyzer.run(max_passes=args.max_passes)

    print("\n=== Intersection Attack Results ===")
    print(f"Total rings analyzed:    {stats['total_rings']}")
    print(f"Fully resolved:          {stats['fully_resolved']}")
    print(f"Partially reduced:       {stats['partially_reduced']}")
    print(f"Unreduced:               {stats['unreduced']}")
    print(f"Resolution rate:         {stats['resolution_rate']}")
    print(f"Cascade passes:          {stats['passes']}")
    print(f"\nEffective ring size distribution:")
    for size, count in sorted(stats["effective_ring_size_distribution"].items()):
        print(f"  Size {size}: {count}")

    if args.score:
        print("\n=== Training Probabilistic Scorer ===")
        scorer = RingScorer(db, analyzer)
        if scorer.train():
            soft_stats = scorer.soft_cascade(
                confidence_threshold=args.confidence,
            )
            print(f"\nSoft cascade resolved:   {soft_stats['soft_resolved']}")
            print(f"Remaining unresolved:    {soft_stats['remaining_unresolved']}")

            # Recompute stats
            total = stats["total_rings"]
            resolved = len(analyzer.resolved)
            print(f"\nFinal resolution rate:   {resolved}/{total} "
                  f"({resolved / total * 100:.2f}%)" if total else "")

    db.close()


def cmd_predict(args):
    """Train ML scorer and save predictions without applying soft cascade."""
    db = Database(args.db)
    analyzer = Analyzer(db)
    stats = analyzer.run(max_passes=args.max_passes)

    print("\n=== Prediction-Only ML Scoring ===")
    print(f"Total rings analyzed:    {stats['total_rings']}")
    print(f"Deterministically done:  {stats['fully_resolved']}")
    print(f"Unreduced:               {stats['unreduced']}")
    print(f"Confidence threshold:    {args.confidence}")

    scorer = RingScorer(db, analyzer)
    if scorer.train():
        predictions = scorer.score_unresolved(confidence_threshold=args.confidence)
        print(f"\nSaved predictions:       {len(predictions)}")
        print("Applied ML to cascade:   no")

        if predictions and args.show > 0:
            print(f"\nTop {min(args.show, len(predictions))} predictions:")
            for pred in predictions[:args.show]:
                amount, index = pred["predicted_output"]
                print(
                    f"  {pred['key_image'][:16]}... -> "
                    f"(amount={amount}, index={index}) "
                    f"confidence={pred['confidence']:.4f} "
                    f"ring_size={pred['ring_size']}"
                )

    db.close()


def cmd_export(args):
    db = Database(args.db)
    analyzer = Analyzer(db)
    analyzer.run(max_passes=args.max_passes)
    analyzer.export_json(args.output)
    print(f"Results exported to {args.output}")
    db.close()


def cmd_diagnose(args):
    db = Database(args.db)
    analyzer = Analyzer(db)
    analyzer.run(max_passes=args.max_passes)

    results = analyzer.diagnose_empty_rings()
    if not results:
        print("No empty rings — nothing to diagnose.")
    else:
        print(f"\n=== {len(results)} Empty Rings Found ===")
        for r in results:
            print(f"\nKey image: {r['key_image']}")
            print(f"  TX: {r['tx_hash']}")
            print(f"  Original ring size: {len(r['original_members'])}")
            for e in r['eliminations']:
                amt, idx = e['output']
                if e['eliminated_by']:
                    print(f"  Output (amt={amt}, idx={idx}): spent in {e['eliminated_by'][:16]}...")
                else:
                    print(f"  Output (amt={amt}, idx={idx}): NO eliminator found")
    db.close()


def cmd_verify(args):
    db = Database(args.db)
    analyzer = Analyzer(db)
    analyzer.run(max_passes=args.max_passes)

    scorer = RingScorer(db, analyzer)
    results = scorer.verify_predictions()

    print("\n=== ML Prediction Verification ===")
    print(f"Checked:     {results['checked']}")
    print(f"Correct:     {results['correct']}")
    print(f"Wrong:       {results['wrong']}")
    print(f"Accuracy:    {results['accuracy']}")

    # Show overall prediction stats
    stats = db.get_prediction_stats()
    print(f"\nTotal predictions:  {stats['total_predictions']}")
    print(f"Verified so far:    {stats['verified']}")
    print(f"Still unverified:   {stats['unverified']}")
    if stats['verified'] > 0:
        print(f"Cumulative accuracy: {stats['accuracy']}")

    db.close()


def cmd_export_viz(args):
    if not 0 <= getattr(args, "prediction_limit", 200) <= 1000:
        raise ValueError("prediction limit must be between 0 and 1000")
    if not 0 <= getattr(args, "evidence_trace_limit", 50) <= 200:
        raise ValueError("evidence trace limit must be between 0 and 200")
    db = Database(args.db)
    try:
        analyzer = Analyzer(db)
        stats = analyzer.run(max_passes=args.max_passes)
        db_stats = db.get_stats()
        stats = {**stats, **evidence_summary(db, analyzer)}
        ml_stats = db.get_prediction_stats()
        ml_training = None

        if args.include_ml_training:
            scorer = RingScorer(db, analyzer)
            if scorer.train():
                ml_training = scorer.training_metrics

        # Load existing history or start fresh
        data_path = os.path.join(args.output_dir, "data.json")
        history = []
        if os.path.exists(data_path):
            try:
                with open(data_path) as f:
                    existing = json.load(f)
                    stored_history = existing.get("history", []) if isinstance(existing, dict) else []
                    history = [item for item in stored_history if isinstance(item, dict)] if isinstance(stored_history, list) else []
            except (json.JSONDecodeError, KeyError):
                pass

        # Keep changes in evidence visible even when no new blocks were scanned.
        snapshot = {
            "dataset_id": db.get_dataset_id(),
            "blocks_scanned": db_stats["blocks_scanned"],
            "total_rings": stats["total_rings"],
            "fully_resolved": stats["fully_resolved"],
            "partially_reduced": stats["partially_reduced"],
            "unreduced": stats["unreduced"],
            "resolution_rate": stats["resolution_rate"],
            "passes": stats["passes"],
            "deterministic_resolutions": stats["deterministic_resolutions"],
            "hypothesis_resolutions": stats["hypothesis_resolutions"],
            "conflict_rings": stats["conflict_rings"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if not history or any(history[-1].get(key) != value for key, value in snapshot.items()
                              if key != "timestamp"):
            history.append(snapshot)

        output = {
            "schema_version": 2,
            "scope": {
                "dataset_id": db.get_dataset_id(),
                "scan_start": db.conn.execute("SELECT MIN(height) FROM blocks").fetchone()[0],
                "scan_end": db.conn.execute("SELECT MAX(height) FROM blocks").fetchone()[0],
                "exported_at": datetime.now(timezone.utc).isoformat(),
            },
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "summary": {
                **stats,
                "blocks_scanned": db_stats["blocks_scanned"],
                "transactions": db_stats["transactions"],
                "ring_members": db_stats["ring_members"],
            },
            "ml_predictions": ml_stats,
            "ml_training": ml_training,
            "history": history,
            "prediction_browser": prediction_browser(
                db, limit=getattr(args, "prediction_limit", 200),
                trace_limit=getattr(args, "evidence_trace_limit", 50),
            ),
        }

        os.makedirs(args.output_dir, exist_ok=True)
        # Readers should see either the old complete snapshot or the new one.
        with tempfile.NamedTemporaryFile(mode="w", dir=args.output_dir, suffix=".json.tmp", delete=False) as f:
            temporary_path = f.name
            try:
                json.dump(output, f, indent=2, allow_nan=False)
                f.close()
                os.replace(temporary_path, data_path)
            finally:
                if os.path.exists(temporary_path):
                    os.unlink(temporary_path)

        print(f"Dashboard data written to {data_path}")
        print(f"Serve locally: python -m http.server 8000 --bind 127.0.0.1 --directory {shlex.quote(args.output_dir)}")
        print("Open http://127.0.0.1:8000 (serve the dashboard HTML/assets from the same directory).")
    finally:
        db.close()


def cmd_status(args):
    db = Database(args.db)
    stats = db.get_stats()

    print("=== Database Status ===")
    print(f"Blocks scanned:      {stats['blocks_scanned']}")
    print(f"Transactions:        {stats['transactions']}")
    print(f"Ring members:        {stats['ring_members']}")
    print(f"Unique key images:   {stats['unique_key_images']}")
    print(f"Resolved spends:     {stats['resolved_spends']}")

    if stats["blocks_scanned"] > 0:
        progress = db.get_scan_progress()
        print(f"Latest block:        {progress}")

    db.close()


def cmd_brain(args):
    uri = Path(args.db).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("BEGIN")
        brain = Brain(connection)
        if args.key_image:
            explanation = brain.explain(args.key_image)
            result = asdict(explanation)
            resolution = explanation.memory.resolution
            result["memory"]["resolution_kind"] = (
                "deterministic" if resolution.deterministic else "hypothesis"
            ) if resolution else None
            result["related_rings"] = [
                asdict(ring) for ring in brain.related_rings(args.key_image, args.related_limit)
            ]
            result["lineage"] = asdict(brain.trace(args.key_image, max_nodes=args.trace_limit))
        else:
            result = brain.summary()
        print(json.dumps(result, indent=2))
    finally:
        connection.close()


def non_negative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def main():
    parser = argparse.ArgumentParser(
        description="Monero Ring Signature Intersection Attack Tool",
    )
    parser.add_argument("--node", default="http://127.0.0.1:18081",
                        help="Monero node RPC URL (default: http://127.0.0.1:18081)")
    parser.add_argument("--db", default="monero_analysis.db",
                        help="SQLite database path (default: monero_analysis.db)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    scan_parser = subparsers.add_parser("scan", help="Fetch blocks and store ring data")
    scan_parser.add_argument("--start", type=int, default=0,
                             help="Start block height (default: 0)")
    scan_parser.add_argument("--end", type=int, default=0,
                             help="End block height (default: chain tip)")
    scan_parser.add_argument("--batch-size", type=int, default=100,
                             help="Blocks per DB commit (default: 100)")
    scan_parser.add_argument("--delay", type=int, default=0,
                             help="Delay between RPC calls in ms (default: 0)")
    scan_parser.add_argument("--log-every", type=int, default=100,
                             help="Log progress every N blocks (default: 100)")

    # analyze
    analyze_parser = subparsers.add_parser("analyze", help="Run intersection attack")
    analyze_parser.add_argument("--max-passes", type=int, default=100,
                                help="Max cascade passes (default: 100)")
    analyze_parser.add_argument("--score", action="store_true",
                                help="Enable ML soft cascade after deterministic analysis; mutates resolved_spends")
    analyze_parser.add_argument("--confidence", type=float, default=0.95,
                                help="Confidence threshold for probabilistic resolution (default: 0.95)")

    # predict
    predict_parser = subparsers.add_parser(
        "predict",
        help="Save ML predictions without applying them to the cascade",
    )
    predict_parser.add_argument("--max-passes", type=int, default=100,
                                help="Max deterministic cascade passes before scoring (default: 100)")
    predict_parser.add_argument("--confidence", type=float, default=0.95,
                                help="Confidence threshold for saved predictions (default: 0.95)")
    predict_parser.add_argument("--show", type=int, default=10,
                                help="Number of top predictions to print (default: 10)")

    # export
    export_parser = subparsers.add_parser("export", help="Export results to JSON")
    export_parser.add_argument("--output", default="results.json",
                               help="Output file path (default: results.json)")
    export_parser.add_argument("--max-passes", type=int, default=100)

    # verify
    verify_parser = subparsers.add_parser("verify", help="Verify ML predictions against deterministic resolutions")
    verify_parser.add_argument("--max-passes", type=int, default=100)

    # diagnose
    diag_parser = subparsers.add_parser("diagnose", help="Investigate empty rings")
    diag_parser.add_argument("--max-passes", type=int, default=100)

    # export-viz
    viz_parser = subparsers.add_parser("export-viz", help="Export data for GitHub Pages dashboard")
    viz_parser.add_argument("--output-dir", default="docs",
                            help="Output directory (default: docs)")
    viz_parser.add_argument("--max-passes", type=int, default=100)
    viz_parser.add_argument("--include-ml-training", action="store_true",
                            help="Train scorer and include holdout/feature-importance data")
    viz_parser.add_argument("--prediction-limit", type=non_negative_int, default=200,
                            help="Newest historical scoring records to export, 0–1000 (default: 200)")
    viz_parser.add_argument("--evidence-trace-limit", type=non_negative_int, default=50,
                            help="Events per evidence trace, 0–200 (default: 50)")

    # status
    subparsers.add_parser("status", help="Show database statistics")

    # brain
    brain_parser = subparsers.add_parser("brain", help="Inspect the evidence graph (read-only)")
    brain_parser.add_argument("--key-image", help="Explain a ring and recall its evidence")
    brain_parser.add_argument("--related-limit", type=non_negative_int, default=20,
                              help="Maximum related rings to show (default: 20)")
    brain_parser.add_argument("--trace-limit", type=non_negative_int, default=100,
                              help="Maximum resolution events to trace (default: 100)")

    args = parser.parse_args()
    setup_logging(args.verbose)

    try:
        if args.command == "scan":
            cmd_scan(args)
        elif args.command == "analyze":
            cmd_analyze(args)
        elif args.command == "predict":
            cmd_predict(args)
        elif args.command == "export":
            cmd_export(args)
        elif args.command == "verify":
            cmd_verify(args)
        elif args.command == "diagnose":
            cmd_diagnose(args)
        elif args.command == "export-viz":
            cmd_export_viz(args)
        elif args.command == "status":
            cmd_status(args)
        elif args.command == "brain":
            try:
                cmd_brain(args)
            except (sqlite3.Error, KeyError) as exc:
                parser.exit(1, f"Brain error: {exc}\n")
    except KeyboardInterrupt:
        print("\nInterrupted. Progress has been saved.")
        sys.exit(1)


if __name__ == "__main__":
    main()
