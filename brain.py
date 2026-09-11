"""A queryable evidence graph backed by the analysis database."""

from dataclasses import dataclass
from collections import deque
import sqlite3


@dataclass(frozen=True, order=True)
class Output:
    amount: int
    index: int


@dataclass(frozen=True)
class RingInput:
    tx_hash: str
    input_index: int
    block_height: int | None


@dataclass(frozen=True)
class Resolution:
    output: Output
    pass_num: int | None
    confidence: float | None

    @property
    def deterministic(self):
        return self.confidence == 1.0 and self.pass_num is not None and self.pass_num >= 0


@dataclass(frozen=True)
class Prediction:
    output: Output
    confidence: float
    created_at: str | None
    verified: bool
    correct: bool | None


@dataclass(frozen=True)
class RingMemory:
    key_image: str
    members: tuple[Output, ...]
    inputs: tuple[RingInput, ...]
    resolution: Resolution | None
    prediction: Prediction | None


@dataclass(frozen=True)
class RelatedRing:
    key_image: str
    shared_outputs: int


@dataclass(frozen=True)
class CandidateEvidence:
    output: Output
    eliminated_by: tuple[str, ...]


@dataclass(frozen=True)
class RingExplanation:
    memory: RingMemory
    candidates: tuple[CandidateEvidence, ...]
    remaining_candidates: tuple[Output, ...]
    conflicts: tuple[str, ...]


@dataclass(frozen=True)
class Dependency:
    eliminated_output: Output
    source_event_id: int


@dataclass(frozen=True)
class ResolutionEvent:
    event_id: int
    key_image: str
    resolution: Resolution
    method: str
    scan_height: int | None
    recorded_at: str
    dependencies: tuple[Dependency, ...]


@dataclass(frozen=True)
class ResolutionTrace:
    root_event_id: int | None
    events: tuple[ResolutionEvent, ...]
    complete: bool
    truncated: bool
    missing_event_ids: tuple[int, ...]


class Brain:
    """Read-through graph: rings and outputs are nodes; membership links them.

    SQLite owns persistence. Returned memories are immutable snapshots; recall
    again after scanning or analysis to see new evidence. This layer executes
    SELECTs only and never runs a cascade or verifies a prediction.
    """

    def __init__(self, connection: sqlite3.Connection):
        self.conn = connection

    def recall(self, key_image: str) -> RingMemory:
        rows = self.conn.execute(
            "SELECT DISTINCT rm.amount, rm.global_output_index, rm.tx_hash, "
            "rm.input_index, tx.block_height FROM ring_members AS rm "
            "LEFT JOIN transactions AS tx ON tx.tx_hash = rm.tx_hash "
            "WHERE rm.key_image = ?",
            (key_image,),
        ).fetchall()
        if not rows:
            raise KeyError(f"Unknown ring: {key_image}")

        members = tuple(sorted({Output(row[0], row[1]) for row in rows}))
        inputs = {RingInput(row[2], row[3], row[4]) for row in rows}
        resolved = self.conn.execute(
            "SELECT real_amount, real_output_index, resolved_at_pass, confidence "
            "FROM resolved_spends WHERE key_image = ?", (key_image,),
        ).fetchone()
        predicted = self.conn.execute(
            "SELECT predicted_amount, predicted_output_index, confidence, "
            "created_at, verified, correct FROM ml_predictions WHERE key_image = ?",
            (key_image,),
        ).fetchone()
        return RingMemory(
            key_image=key_image,
            members=members,
            inputs=tuple(sorted(inputs, key=lambda item: (item.tx_hash, item.input_index))),
            resolution=Resolution(Output(*resolved[:2]), *resolved[2:]) if resolved else None,
            prediction=Prediction(
                Output(*predicted[:2]), predicted[2], predicted[3], bool(predicted[4]),
                bool(predicted[5]) if predicted[5] is not None else None,
            ) if predicted else None,
        )

    def related_rings(self, key_image: str, limit: int = 20) -> tuple[RelatedRing, ...]:
        """Find rings sharing outputs, ranked by the number of shared members."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        self.recall(key_image)
        rows = self.conn.execute(
            "WITH members AS ("
            "  SELECT DISTINCT amount, global_output_index FROM ring_members WHERE key_image = ?"
            "), shared AS ("
            "  SELECT DISTINCT rm.key_image, rm.amount, rm.global_output_index "
            "  FROM members JOIN ring_members AS rm "
            "  ON rm.amount = members.amount AND rm.global_output_index = members.global_output_index "
            "  WHERE rm.key_image != ?"
            ") SELECT key_image, COUNT(*) FROM shared GROUP BY key_image "
            "ORDER BY COUNT(*) DESC, key_image LIMIT ?",
            (key_image, key_image, limit),
        )
        return tuple(RelatedRing(*row) for row in rows)

    def explain(self, key_image: str) -> RingExplanation:
        """Explain candidate eliminations using stored deterministic resolutions.

        These are current supporting records, not historical cascade traces.
        Pass/confidence metadata carries the same assumptions as the analyzer.
        """
        memory = self.recall(key_image)
        eliminators = {member: [] for member in memory.members}
        # Membership validation keeps orphaned or invalid resolution records
        # from eliminating candidates in an otherwise unrelated ring.
        rows = self.conn.execute(
            "SELECT rs.key_image, rs.real_amount, rs.real_output_index "
            "FROM resolved_spends AS rs "
            "WHERE rs.confidence = 1.0 AND rs.resolved_at_pass >= 0 AND rs.key_image != ? "
            "AND (rs.real_amount, rs.real_output_index) IN ("
            "  SELECT amount, global_output_index FROM ring_members WHERE key_image = ?"
            ") AND EXISTS ("
            "  SELECT 1 FROM ring_members AS source WHERE source.key_image = rs.key_image "
            "  AND source.amount = rs.real_amount AND source.global_output_index = rs.real_output_index"
            ") ORDER BY rs.key_image",
            (key_image, key_image),
        )
        for source_ki, amount, index in rows:
            eliminators[Output(amount, index)].append(source_ki)

        candidates = tuple(
            CandidateEvidence(member, tuple(eliminators[member])) for member in memory.members
        )
        remaining = tuple(member for member in memory.members if not eliminators[member])
        conflicts = []
        resolution = memory.resolution
        if resolution is not None:
            if resolution.output not in memory.members:
                conflicts.append("Stored resolution points outside this ring.")
            elif resolution.deterministic:
                if eliminators[resolution.output]:
                    conflicts.append("Stored deterministic resolution is also claimed by another ring.")
                remaining = tuple(member for member in remaining if member == resolution.output)
        if memory.prediction is not None and memory.prediction.output not in memory.members:
            conflicts.append("Stored prediction points outside this ring.")
        if not remaining:
            conflicts.append("No candidates remain under the stored deterministic evidence.")
        return RingExplanation(memory, candidates, remaining, tuple(conflicts))

    def trace(self, key_image: str, max_nodes: int = 100) -> ResolutionTrace:
        """Follow immutable dependency IDs for the current resolution.

        Complete means all dependency events are available with known methods,
        not that the conclusion is deterministic. Legacy origins remain unknown.
        """
        if isinstance(max_nodes, bool) or not isinstance(max_nodes, int) or max_nodes < 0:
            raise ValueError("max_nodes must be a non-negative integer")
        memory = self.recall(key_image)
        has_history = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'resolution_events'"
        ).fetchone()
        if memory.resolution is None or not has_history:
            return ResolutionTrace(None, (), False, False, ())

        root = self.conn.execute(
            "SELECT id, real_amount, real_output_index, resolved_at_pass, confidence "
            "FROM resolution_events WHERE key_image = ? ORDER BY id DESC LIMIT 1", (key_image,),
        ).fetchone()
        resolution = memory.resolution
        if root is None or tuple(root[1:]) != (
            resolution.output.amount, resolution.output.index, resolution.pass_num, resolution.confidence,
        ):
            return ResolutionTrace(None, (), False, False, ())

        pending = deque([root[0]])
        seen = {root[0]}
        events, missing = [], []
        complete = True
        while pending and len(events) + len(missing) < max_nodes:
            event_id = pending.popleft()
            row = self.conn.execute(
                "SELECT key_image, real_amount, real_output_index, resolved_at_pass, confidence, "
                "method, scan_height, recorded_at FROM resolution_events WHERE id = ?", (event_id,),
            ).fetchone()
            if row is None:
                missing.append(event_id)
                complete = False
                continue
            dependencies = tuple(
                Dependency(Output(amount, index), source_id)
                for amount, index, source_id in self.conn.execute(
                    "SELECT eliminated_amount, eliminated_output_index, source_event_id "
                    "FROM resolution_dependencies WHERE event_id = ? "
                    "ORDER BY eliminated_amount, eliminated_output_index", (event_id,),
                )
            )
            events.append(ResolutionEvent(
                event_id, row[0], Resolution(Output(row[1], row[2]), row[3], row[4]),
                row[5], row[6], row[7], dependencies,
            ))
            if row[5] == "legacy":
                complete = False
            for dependency in dependencies:
                # Writers only link to older events. Reject corrupted forward/cyclic links.
                if dependency.source_event_id >= event_id:
                    complete = False
                if dependency.source_event_id not in seen:
                    seen.add(dependency.source_event_id)
                    pending.append(dependency.source_event_id)
        return ResolutionTrace(root[0], tuple(events), complete and not pending, bool(pending), tuple(missing))

    def summary(self) -> dict:
        rings, memberships = self.conn.execute(
            "SELECT COUNT(DISTINCT key_image), COUNT(*) FROM ("
            "SELECT DISTINCT key_image, amount, global_output_index FROM ring_members)"
        ).fetchone()
        outputs = self.conn.execute(
            "SELECT COUNT(*) FROM (SELECT DISTINCT amount, global_output_index FROM ring_members)"
        ).fetchone()[0]
        resolutions, deterministic = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN confidence = 1.0 AND resolved_at_pass >= 0 "
            "THEN 1 ELSE 0 END), 0) FROM resolved_spends"
        ).fetchone()
        predictions = self.conn.execute("SELECT COUNT(*) FROM ml_predictions").fetchone()[0]
        return {
            "rings": rings,
            "outputs": outputs,
            "memberships": memberships,
            "deterministic_resolutions": deterministic,
            "hypothesis_resolutions": resolutions - deterministic,
            "predictions": predictions,
        }
