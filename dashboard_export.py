"""Bounded, JSON-safe dashboard records. Queries never run analysis themselves."""

from dataclasses import asdict
from itertools import groupby

from brain import Brain


MAX_PREDICTIONS = 1000
CANDIDATE_LIMIT = 128
INPUT_LIMIT = 32
ELIMINATOR_LIMIT = 20


def evidence_summary(db, analyzer, *, era_accumulator=None):
    """Count existing rings, keeping hypothesis claims and conflict flags separate.

    Reduction counts describe the analyzer state (which may use hypotheses).
    Conflict checks describe current deterministic evidence, as Brain.explain does.
    Orphan claims never increase the ring denominator.
    """
    claims = {
        ki: ((amount, index), confidence == 1.0 and pass_num is not None and pass_num >= 0)
        for ki, amount, index, pass_num, confidence in db.conn.execute(
            "SELECT key_image, real_amount, real_output_index, resolved_at_pass, confidence "
            "FROM resolved_spends"
        )
    }
    valid_owners = {}
    for ki, amount, index in db.conn.execute(
        "SELECT rs.key_image, rs.real_amount, rs.real_output_index FROM resolved_spends rs "
        "WHERE confidence = 1.0 AND resolved_at_pass >= 0 AND EXISTS ("
        "SELECT 1 FROM ring_members rm WHERE rm.key_image = rs.key_image "
        "AND rm.amount = rs.real_amount AND rm.global_output_index = rs.real_output_index)"
    ):
        valid_owners.setdefault((amount, index), set()).add(ki)
    predictions = {
        ki: (amount, index) for ki, amount, index in db.conn.execute(
            "SELECT key_image, predicted_amount, predicted_output_index FROM ml_predictions"
        )
    }
    counts = dict(total_rings=0, deterministic_resolutions=0, hypothesis_resolutions=0,
                  unresolved_reduced=0, unresolved_unchanged=0, conflict_rings=0,
                  original_singleton_rings=0, original_multimember_rings=0,
                  deterministic_singleton_resolutions=0, deterministic_multimember_resolutions=0)
    observed_claims = 0
    rows = db.conn.execute(
        "SELECT rm.key_image, rm.amount, rm.global_output_index, rm.tx_hash, rm.input_index, t.block_height "
        "FROM ring_members rm LEFT JOIN transactions t ON t.tx_hash = rm.tx_hash ORDER BY rm.key_image"
        if era_accumulator is not None else
        "SELECT key_image, amount, global_output_index FROM ring_members ORDER BY key_image"
    )
    for ki, members in groupby(rows, key=lambda row: row[0]):
        original, contexts = set(), set()
        membership_count = 0
        for member in members:
            membership_count += 1
            original.add((member[1], member[2]))
            if era_accumulator is not None:
                contexts.add(tuple(member[3:6]))
        counts["total_rings"] += 1
        cohort = "singleton" if len(original) == 1 else "multimember"
        counts[f"original_{cohort}_rings"] += 1
        claim = claims.get(ki)
        if claim is not None:
            observed_claims += 1
            category = "deterministic_resolutions" if claim[1] else "hypothesis_resolutions"
            counts[category] += 1
            if claim[1]:
                counts[f"deterministic_{cohort}_resolutions"] += 1
        else:
            remaining = analyzer.rings.get(ki, original)
            category = "unresolved_reduced" if len(remaining) < len(original) else "unresolved_unchanged"
            counts[category] += 1
        supported = {output for output in original if not (valid_owners.get(output, set()) - {ki})}
        conflict = bool(claim and claim[0] not in original)
        if claim and claim[1] and claim[0] in original:
            supported.intersection_update({claim[0]})
        if not supported or (ki in predictions and predictions[ki] not in original):
            conflict = True
        counts["conflict_rings"] += int(conflict)
        if era_accumulator is not None:
            era_accumulator.add_ring(contexts, len(original), category, conflict, membership_count)
    counts["orphan_resolution_claims"] = len(claims) - observed_claims
    counts["fully_resolved"] = counts["deterministic_resolutions"] + counts["hypothesis_resolutions"]
    counts["partially_reduced"] = counts["unresolved_reduced"]
    counts["unreduced"] = counts["unresolved_unchanged"]
    total = counts["total_rings"]
    counts["resolution_rate"] = f"{100 * counts['fully_resolved'] / total:.2f}%" if total else "N/A"
    counts["deterministic_resolution_rate"] = (
        f"{100 * counts['deterministic_resolutions'] / total:.2f}%" if total else "N/A"
    )
    return counts


def lossless_identities(value):
    """Output identity integers can exceed JavaScript's exact integer range."""
    identity_fields = {"amount", "index", "predicted_amount", "predicted_output_index",
                       "real_amount", "real_output_index", "global_output_index",
                       "actual_amount", "actual_output_index", "output_index"}
    if isinstance(value, dict):
        return {
            key: str(item) if key in identity_fields and isinstance(item, int)
            else lossless_identities(item) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [lossless_identities(item) for item in value]
    return value


def prediction_browser(db, limit=200, trace_limit=50, related_limit=10):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 <= limit <= MAX_PREDICTIONS:
        raise ValueError(f"prediction limit must be between 0 and {MAX_PREDICTIONS}")
    for name, value, maximum in (("trace", trace_limit, 200), ("related", related_limit, 50)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError(f"{name} limit must be between 0 and {maximum}")
    history = db.get_prediction_history(limit=limit)
    brain = Brain(db.conn)
    evidence_cache = {}
    rows = []
    for record in history["rows"]:
        row = dict(record)
        ki = row["key_image"]
        row["candidates_total"] = len(row.get("candidates", []))
        row["candidates_truncated"] = row["candidates_total"] > CANDIDATE_LIMIT
        row["candidates"] = sorted(
            row.get("candidates", []), key=lambda item: item["score"], reverse=True,
        )[:CANDIDATE_LIMIT]
        if ki not in evidence_cache:
            try:
                explanation = brain.explain(ki)
                evidence = asdict(explanation)
                memory = explanation.memory
                evidence["memory"]["resolution_kind"] = (
                    "deterministic" if memory.resolution.deterministic else "hypothesis"
                ) if memory.resolution else None
                evidence["candidate_count"] = len(memory.members)
                evidence["input_count"] = len(memory.inputs)
                evidence["remaining_candidate_count"] = len(explanation.remaining_candidates)
                evidence["candidates_truncated"] = len(memory.members) > CANDIDATE_LIMIT
                evidence["inputs_truncated"] = len(memory.inputs) > INPUT_LIMIT
                evidence["remaining_candidates_truncated"] = len(explanation.remaining_candidates) > CANDIDATE_LIMIT
                evidence["memory"]["members"] = evidence["memory"]["members"][:CANDIDATE_LIMIT]
                evidence["memory"]["inputs"] = evidence["memory"]["inputs"][:INPUT_LIMIT]
                evidence["candidates"] = evidence["candidates"][:CANDIDATE_LIMIT]
                evidence["remaining_candidates"] = evidence["remaining_candidates"][:CANDIDATE_LIMIT]
                for candidate in evidence["candidates"]:
                    candidate["eliminated_by_count"] = len(candidate["eliminated_by"])
                    candidate["eliminated_by_truncated"] = len(candidate["eliminated_by"]) > ELIMINATOR_LIMIT
                    candidate["eliminated_by"] = candidate["eliminated_by"][:ELIMINATOR_LIMIT]
                related = brain.related_rings(ki, limit=related_limit + 1)
                evidence["related_rings"] = [asdict(ring) for ring in related[:related_limit]]
                evidence["related_rings_limit"] = related_limit
                evidence["related_rings_truncated"] = len(related) > related_limit
                evidence["lineage"] = asdict(brain.trace(ki, max_nodes=trace_limit))
                # Bound event dependency arrays independently of ancestry length.
                for event in evidence["lineage"]["events"]:
                    event["dependency_count"] = len(event["dependencies"])
                    event["dependencies_truncated"] = len(event["dependencies"]) > CANDIDATE_LIMIT
                    event["dependencies"] = event["dependencies"][:CANDIDATE_LIMIT]
                evidence_cache[ki] = (evidence, None, sorted({item.tx_hash for item in memory.inputs}),
                                      min((item.block_height for item in memory.inputs
                                           if item.block_height is not None), default=None), len(memory.members))
            except KeyError:
                evidence_cache[ki] = (None, "Ring is absent from this dataset snapshot.", [], None, None)
        evidence, error, hashes, height, ring_size = evidence_cache[ki]
        row.update(evidence=evidence, evidence_error=error, tx_hashes=hashes[:INPUT_LIMIT],
                   block_height=height, original_ring_size=row.get("original_ring_size") or ring_size)
        rows.append(row)
    return lossless_identities({
        "total": history["total"], "exported": len(rows), "limit": limit,
        "order": "newest_first", "rows": rows, "runs": history["runs"],
        "scope": "All historical scoring records, including below-threshold scores; latest records exported first.",
        "evidence_scope": "Current stored evidence at export time; candidate scores belong to the selected run.",
        "limits": {"candidates": CANDIDATE_LIMIT, "inputs": INPUT_LIMIT,
                   "eliminators": ELIMINATOR_LIMIT, "trace": trace_limit, "related": related_limit},
    })
