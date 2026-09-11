---
summary: SQLite entities, deterministic classification, and evidence lineage semantics.
status: maintained
reviewed: 2026-09-11
---

# Storage and evidence

Parent: [architecture](index.md).

Sources: [schema and Database methods](../../models.py),
[evidence records and Brain methods](../../brain.py),
[cascade recording](../../analyzer.py).

| Table | Meaning |
| --- | --- |
| `blocks` | Stored block heights and headers |
| `transactions` | Transaction metadata and block association |
| `ring_members` | Key-image membership, transaction/input context, amount and output index |
| `resolved_spends` | Current resolution claim per key image |
| `ml_predictions` | Compatibility cache of the latest accepted prediction per ring |
| `prediction_runs` | Scoring run, dataset identity, cutoff, code/features/settings metadata |
| `prediction_records`, `prediction_candidates` | Immutable per-run choices, acceptance status, scores and frozen candidate features |
| `prediction_artifacts` | Compressed model/scaler bytes addressed by SHA-256, excluded from dashboard exports |
| `prediction_verifications` | Recorded outcomes, observed label/event and verification timing/provenance |
| `prediction_current` | Links the compatibility cache to its accepted history record |
| `analysis_metadata` | Persistent dataset identity and migration marker |
| `resolution_events` | Recorded resolution claims with method, pass, confidence, scan height, and time |
| `resolution_dependencies` | Each eliminated output and the source event used to eliminate it |

An output's identity is `(amount, global_output_index)`. Matching on index alone
can connect unrelated outputs. Membership rows may repeat; graph queries
deduplicate where they count distinct members or shared outputs.

## Claims and history

The stored deterministic predicate is `confidence = 1.0 AND resolved_at_pass >= 0`.
Known event methods are `ring_size_one`, `cascade`, `ml_prediction`,
`soft_cascade`, and `legacy`. The writer validates known-method membership and
dependencies, and prevents deterministic cascades from depending on hypotheses.

Events retain original dependency IDs when current claims change. Unchanged
claims with matching methods and dependencies reuse their events. Legacy imports
preserve claims whose original reasoning is unknown; import time is not the
historical discovery time. Immutability is an application convention, not a
database-wide prohibition on direct SQL updates.

Prediction history imports existing cache rows once, retaining creation times and
outcomes while marking unknown model, cutoff, and verification provenance.
Re-scoring preserves prior records, including rejected/below-threshold scores.
Verification checks each outstanding accepted history record against its own
chosen output. Historical outcomes do not overwrite the latest cache entry.
Later-scan timing is recorded separately from unknown or same-scan verification;
it alone does not prove a clean forward experiment.

## Evidence API

- `recall()` returns an immutable snapshot of members, inputs, resolution, and prediction.
- `related_rings()` ranks neighboring rings by distinct shared outputs.
- `explain()` uses current stored deterministic claims to explain eliminations and conflicts; hypotheses do not eliminate candidates here.
- `trace()` follows recorded event ancestry. `complete` means available ancestry with known methods, not an independently proven conclusion. Limits, missing events, stale claims, and legacy origins affect completeness.
- `summary()` counts graph entities and separates deterministic from hypothesis resolutions.

The CLI `brain` command opens an existing database with SQLite `mode=ro`.
Regular `Database()` initialization creates schema and configures WAL mode.
See [development](../workflows/development.md) for the distinction in commands.

[Dashboard export](../../dashboard_export.py) counts only observed rings for its
evidence categories, flags conflicts separately, and bounds prediction/evidence
details. Output identity integers become decimal strings in schema-v2 JSON to
avoid browser precision loss. Its inspector combines frozen scores with explicitly
labeled current evidence; it does not reconstruct historical eliminations.

Behavioral examples: [graph tests](../../tests/test_brain.py),
[lineage tests](../../tests/test_lineage.py). Related:
[evaluation](../research/evaluation.md).
