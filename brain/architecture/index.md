---
summary: Module ownership and the path from node data to stored evidence.
status: maintained
reviewed: 2026-09-11
---

# Architecture

Parent: [repository brain](../index.md).

The Python CLI coordinates RPC ingestion, SQLite persistence, analysis, and ML.
The evidence API queries persisted state on demand; the browser dashboard reads
an exported JSON snapshot.

| Read next | Contents |
| --- | --- |
| [Pipeline](pipeline.md) | Module map, execution flow, scanning and cascade behavior |
| [Storage and evidence](storage-and-evidence.md) | Table roles, identities, hypothesis status, immutable lineage |

Related: [evaluation](../research/evaluation.md) explains what results mean;
[development](../workflows/development.md) describes command effects.

Sources: [main.py](../../main.py), [models.py](../../models.py),
[brain.py](../../brain.py).
