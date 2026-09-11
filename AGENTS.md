# Repository knowledge

Start with [brain/index.md](brain/index.md), then follow only the topic links
relevant to the task. Check linked source code before relying on a behavioral
claim. The Markdown brain describes the repository; `brain.py` implements the
SQLite evidence graph.

When changing behavior, update the affected knowledge page and its parent summary
if needed. Follow [maintenance](brain/workflows/maintenance.md) for sources,
uncertainty, and document organization. Preserve unrelated working-tree changes.

For commands and their database effects, read
[development](brain/workflows/development.md). For analysis or ML changes, also
read [evaluation](brain/research/evaluation.md). Keep output identity as
`(amount, index)` and preserve the distinction between deterministic resolutions
and ML hypotheses.
