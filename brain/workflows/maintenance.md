---
summary: How agents navigate, add, review, and reconcile durable repository knowledge.
status: maintained
reviewed: 2026-09-11
---

# Maintaining the brain

Parent: [workflows](index.md).

This hierarchy is maintained by people and agents editing Markdown. It has no
automatic ingestion, retrieval service, or background updater.

## Reading

Start at the [root index](../index.md), select a topic index, and read the relevant
leaf. Follow cross-links when the task crosses topics. Open the linked code or
test before changing behavior; a review date records a past inspection, not a
guarantee that the page still matches the working tree.

## Writing

1. Update an existing leaf when the knowledge already has a home. Put runtime facts under `architecture/`, measurements and hypotheses under `research/`, and procedures under `workflows/`.
2. Add a leaf when a subject needs independent explanation. Give it one primary parent, a short summary, and relative links to sources and related topics.
3. Link every new leaf from its parent index. Update the parent summary when the branch's meaning changes; update the root when adding a branch.
4. Split pages when unrelated concerns make navigation difficult. Leave summaries and links at the original location instead of duplicating full explanations.
5. Review affected summaries alongside behavior changes. Update `reviewed` only after checking their claims against the relevant sources.

Use this metadata and opening structure for new pages:

```markdown
---
summary: One sentence explaining when to read this page.
status: maintained
reviewed: YYYY-MM-DD
---

# Topic

Parent: [topic index](index.md).

Sources: [implementation](../../module.py).
```

Use `draft` for unverified notes and `archived` for superseded knowledge, with
an explanation and a replacement link when one exists. Relative source paths
depend on page depth. Use standard Markdown links so the structure works without
an Obsidian-specific parser.

## Evidence and conflicts

Describe implemented behavior from source and intended behavior from tests.
Label proposals, hypotheses, and unresolved discrepancies explicitly. If tests,
source, and older notes disagree, inspect the discrepancy and record what is
known; do not silently promote an assumption into a fact.

Keep one canonical explanation per subject and cross-link it elsewhere. Archive
superseded experiment conclusions with their context. Record measurements with
date, revision/local changes, dataset and scan height, exact command, sample
counts, evaluation method, and limitations. Do not present undated historical
numbers as current performance.

Capture durable knowledge, not conversation transcripts or an invented backlog.
Store large raw datasets and generated outputs outside the Markdown hierarchy
and link to them. Preserve uncertainty when compressing notes into summaries.

## Review

Check that relative links resolve, every leaf is reachable from the root, each
leaf links to its parent, and source links support behavioral claims. Review
changed pages for contradictory or duplicated guidance. Documentation-only
changes need link/content checks; runtime changes need the relevant checks from
[development](development.md).

Rebuild the public note graph with `python3 brain_export.py` after editing the
brain, and check it with `python3 brain_export.py --check`. Follow the
[workspace publication checks](research-workspace.md) so the public map and TODO
browser match the Markdown sources. Always test the webpage after updates.
