---
summary: Dated task history, experiment verdicts, website change notices, and the publication workflow.
status: maintained
reviewed: 2026-09-11
---

# Task activity and experiment reporting

Parent: [workflows](index.md). Related: [research conclusions](../research/progress-and-conclusions.md),
[maintenance](maintenance.md), [cloud publication](cloud.md).

The [TODO/results page](../../docs/todos.html) separates a task's checklist state
from the outcome of an experimental claim. It shows dated Git observations,
source-linked evidence, and changes published since this browser last read them.
This is an on-page activity feed, not an email, background push service or
promise of future chat messages. It requires no new VM or notification provider.

## What the dates mean

[task_activity.py](../../task_activity.py) reads the repository's first-parent
Git history. A task's identity is its note path plus explicit task code, or a
normalized title digest when it has no code. For coded or bold-headed tasks, moving a line or editing its body
does not create another task. Unheaded, uncoded tasks use their whole checkbox
text as the title, so changing that text changes identity. Renaming an uncoded
title or moving a task between
notes can create a new identity; no undocumented rename is inferred.

**First recorded** is the commit where the task first appears in the available
history. It is not an invented creation timestamp. A task already checked when
first recorded is an imported completed task; its actual closure date is unknown.
A checked/unchecked transition records a closure/reopening at that commit's UTC
committer time. Removal stays separate from completion, and reintroduction stays
visible. Commit order is the sequence of evidence, even if commit clocks differ.
Uncommitted changes can describe current state but have no invented dated event.

The exported history covers the stated branch and history boundary. It does not
claim to reconstruct off-repository work, deleted Git history, or every side
branch. Source manifests and current task reconciliation prevent a dated export
from silently describing another Markdown snapshot.

## Scientific outcomes

[research/hypotheses.json](../../research/hypotheses.json) is the canonical ledger.
Each research checklist maps to an experimental claim, descriptive measurement,
or engineering objective. Related tasks can share one claim. Execution status
and scientific outcome are separate: a completed investigation may be supported
within its measured scope, refuted, inconclusive, or not applicable to an
engineering objective. An unrun experiment remains untested. Deferring an
infrastructure choice does not refute its potential benefit.

Questions are curated from the existing research record; retrospective reviews
are not presented as prospectively registered experiments. Each entry records
the question, theoretical interpretation, measured conclusion,
source references, saved artifact hashes and denominators when available, and
remaining work. A checked UI task, increasing chain counts or a model's own
predictions do not validate a hypothesis. Historical selective label agreement
remains distinct from forward accuracy; pool block matches remain distinct from
improved forecasting and sender or ownership claims.

## Publication workflow

1. Edit canonical Markdown checklists and the hypothesis ledger together when
   an experiment changes. Record evidence and scope before closing a research
   task. Keep unknown or incomplete outcomes explicit.
2. Commit the source changes to establish real dated Git events. The activity
   builder can preview uncommitted work, but those changes remain undated.
3. Rebuild the public exports with `python3 brain_export.py` and
   `python3 task_activity.py`. Copy the canonical hypothesis JSON byte-for-byte
   to `docs/hypotheses.json`.
4. Run the task-history, ledger and page checks, then commit the generated
   snapshots. Artifact-only commits do not invent task events.
5. Push the reviewed commits for GitHub Pages and run the existing GCP static
   publisher. It validates the committed history snapshot against the archived
   Markdown, checks the ledger copy and publishes without restarting collectors.
6. Check the live TODO page, unseen-change indicator, dated events, hypothesis
   filters and source links. GitHub Pages is a published mirror; the running
   collectors do not edit Markdown or close tasks automatically.

The website checks for published research changes every 60 seconds. Its unread
marker is local to this browser and site origin; a different device or the Pages
mirror has separate read state. A first visit establishes a baseline. Failed
storage or fetches must not hide the available history or erase the last valid
published result.


## Note edits and conclusions

The same export now includes `note_events`: additions, content edits and removals
of Markdown notes, with UTC committer times and content hashes. These are separate
from task creation/closure. A byte-identical note has no new edit event when only
unrelated files change. Note path changes appear as removal/addition, not inferred
renames. The brain reader displays first-recorded and last-committed-edit dates
only when the note hash matches the dated source manifest. The manually entered
`reviewed` field retains its original meaning; it is not an automatic edit clock.

The [conclusions page](../../docs/conclusions.html) shows supported scope,
unknowns, individual experiment conclusions, all 24 feature definitions and a
combined note/task changelog. [Shared explanations](../../docs/research-guide.js)
provide question-mark help by hover, keyboard focus or tap. The inference animation
uses invented candidates, not live solve events; reduced-motion preferences remove
transitions. Stored-block percentage uses `blocks_scanned / (observed_tip + 1)`;
unknown/incompatible counts stay unknown, and block counts do not prove transaction
body completeness. Each point on the resolution timeline is a batch export.

Run [guide checks](../../tests/test_research_guide.js), Git-history tests, ledger
checks and browser interaction checks after changes. The [claim validator](../../research/check_conclusion_claims.py)
and [manifest](../../research/conclusion-claims.json) guard the three frozen
historical cards before static/full publication. New conclusions must retain their
own dates, cohort denominators and evidence limits.
