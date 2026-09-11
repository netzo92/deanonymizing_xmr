---
summary: Entry point for repository knowledge, navigation, and durable context.
status: maintained
reviewed: 2026-09-11
---

# TraceGrove repository brain

TraceGrove scans Monero ring data into SQLite, applies intersection/cascade
analysis, scores unresolved rings with ML, and exposes evidence through a CLI
and a static dashboard. These pages describe the working-tree implementation
reviewed on the date above, including changes that may not yet be committed.

Read this page first, choose a branch below, then open the relevant leaf and its
source links. Loading every page is unnecessary for most tasks.

| Branch | What it answers |
| --- | --- |
| [Architecture](architecture/index.md) | Which modules own scanning, storage, inference, and evidence? |
| [Research](research/index.md) | What do labels and accuracy measurements establish? |
| [Workflows](workflows/index.md) | How do I run checks and maintain this knowledge? |
| [TODOs](tasks/index.md) | Which dashboard and prediction improvements should come next? |

## Essential context

- The app is named TraceGrove. Its intended domain, `tracegrove.io`, is pending registration and DNS setup; the live dashboard remains on [GitHub Pages](https://netzo92.github.io/deanonymizing_xmr/). See the [dashboard guide](../README.md#dashboard).
- A ring is keyed by its key image; an output is identified by both amount and index.
- Stored deterministic classification requires confidence `1.0` and a non-negative pass number. Its validity still depends on the analyzer's assumptions and input data.
- Predictions and cascades derived from them remain hypotheses.
- `brain.py` is the database evidence API. This `brain/` directory is the Markdown knowledge hierarchy; it contains no Python package initializer.
- Source code defines current behavior. Tests supply examples of intended behavior. Markdown summaries and historical metrics can become stale.

## Quick routes

- CLI or scan change → [pipeline](architecture/pipeline.md).
- Schema, graph, or lineage change → [storage and evidence](architecture/storage-and-evidence.md).
- Scorer or accuracy question → [evaluation](research/evaluation.md).
- Commands and tests → [development](workflows/development.md).
- New knowledge or conflicting notes → [maintenance](workflows/maintenance.md).
- Future predictions → [prediction experiments](research/predictions.md) and [grouping research](research/groupings.md).

Sources: [project README](../README.md), [CLI](../main.py),
[agent entry point](../AGENTS.md). This brain is maintained through file edits;
there is no background synchronization or automatic ingestion.
