import json
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class Analyzer:
    def __init__(self, db):
        self.db = db
        self.rings = {}
        self.original_sizes = {}
        self.output_to_key_images = defaultdict(set)
        self.resolved = {}
        self.stats = {}

    def run(self, max_passes=100):
        logger.info("Loading rings from database...")
        self.rings = self.db.get_all_rings()
        total_rings = len(self.rings)
        logger.info(f"Loaded {total_rings} rings")

        for ki in self.rings:
            self.original_sizes[ki] = len(self.rings[ki])

        logger.info("Building inverse index (output → key_images)...")
        for ki, members in self.rings.items():
            for output_idx in members:
                self.output_to_key_images[output_idx].add(ki)
        logger.info(f"Inverse index built: {len(self.output_to_key_images)} unique outputs")

        # Phase 1: Resolve all ring-size-1 rings FIRST (ground truth —
        # the sole member IS the real spend). Must happen before any
        # eliminations, otherwise another ring's resolution can remove
        # the sole member and produce an impossible size-0 ring.
        size1_resolved = 0
        for ki in list(self.rings):
            if len(self.rings[ki]) == 1:
                real_output = next(iter(self.rings[ki]))
                self.resolved[ki] = real_output
                del self.rings[ki]
                self.db.mark_resolved(ki, real_output, pass_num=0)
                size1_resolved += 1
        logger.info(f"Phase 1: resolved {size1_resolved} ring-size-1 inputs (ground truth)")

        # Phase 2: Load previously resolved spends from DB
        previously_resolved = self.db.get_resolved_spends()
        logger.info(f"Loading {len(previously_resolved)} previously resolved spends")
        for ki, real_output in previously_resolved.items():
            if ki not in self.resolved:
                self.resolved[ki] = real_output
            if ki in self.rings:
                del self.rings[ki]

        # Phase 3: Apply all resolved eliminations
        for ki, real_output in self.resolved.items():
            self._eliminate_output(real_output, ki)

        # Seed work queue with rings that dropped to size 1 from eliminations
        work_queue = set()
        for ki, members in self.rings.items():
            if len(members) == 1:
                work_queue.add(ki)

        logger.info(f"Initial work queue: {len(work_queue)} trivially resolved rings")

        pass_num = 0
        total_resolved = len(previously_resolved)

        while work_queue and pass_num < max_passes:
            pass_num += 1
            newly_resolved = []

            for ki in work_queue:
                if ki in self.rings and len(self.rings[ki]) == 1:
                    real_output = next(iter(self.rings[ki]))
                    newly_resolved.append((ki, real_output))
                    self.resolved[ki] = real_output
                    del self.rings[ki]

            work_queue = set()

            for ki, real_output in newly_resolved:
                self.db.mark_resolved(ki, real_output, pass_num)
                affected = self._eliminate_output(real_output, ki)
                for affected_ki in affected:
                    if affected_ki in self.rings and len(self.rings[affected_ki]) == 1:
                        work_queue.add(affected_ki)

            total_resolved += len(newly_resolved)
            if newly_resolved:
                logger.info(f"Pass {pass_num}: resolved {len(newly_resolved)} rings "
                            f"(total: {total_resolved}/{total_rings})")

            self.db.commit()

        # Compute statistics
        ring_size_dist = defaultdict(int)
        partially_reduced_dist = defaultdict(int)
        unreduced_dist = defaultdict(int)
        for ki, members in self.rings.items():
            current_size = len(members)
            original_size = self.original_sizes.get(ki, current_size)
            ring_size_dist[current_size] += 1
            if current_size < original_size:
                partially_reduced_dist[current_size] += 1
            else:
                unreduced_dist[current_size] += 1
        ring_size_dist[1] = len(self.resolved)

        partially_reduced = sum(
            1 for ki, members in self.rings.items()
            if len(members) < self.original_sizes.get(ki, len(members))
        )

        stored_max_pass = self.db.conn.execute(
            "SELECT MAX(resolved_at_pass) FROM resolved_spends "
            "WHERE resolved_at_pass >= 0"
        ).fetchone()[0]
        cascade_passes = max(pass_num, stored_max_pass or 0)

        self.stats = {
            "total_rings": total_rings,
            "fully_resolved": len(self.resolved),
            "partially_reduced": partially_reduced,
            "unreduced": total_rings - len(self.resolved) - partially_reduced,
            "passes": cascade_passes,
            "effective_ring_size_distribution": dict(sorted(ring_size_dist.items())),
            "partially_reduced_ring_size_distribution": dict(sorted(partially_reduced_dist.items())),
            "unreduced_ring_size_distribution": dict(sorted(unreduced_dist.items())),
            "resolution_rate": f"{len(self.resolved) / total_rings * 100:.2f}%" if total_rings else "N/A",
        }

        return self.stats

    def _eliminate_output(self, output_index, source_ki):
        affected = set()
        for other_ki in self.output_to_key_images.get(output_index, set()):
            if other_ki != source_ki and other_ki in self.rings:
                self.rings[other_ki].discard(output_index)
                if len(self.rings[other_ki]) == 0:
                    logger.warning(f"Ring {other_ki} reduced to 0 members — "
                                   f"possible data inconsistency")
                elif len(self.rings[other_ki]) == 1:
                    affected.add(other_ki)
        return affected

    def diagnose_empty_rings(self):
        """Trace why certain rings were reduced to 0 members."""
        empty = [ki for ki, m in self.rings.items() if len(m) == 0]
        if not empty:
            logger.info("No empty rings found")
            return []

        results = []
        for ki in empty:
            details = self.db.get_ring_member_details(ki)
            original_members = sorted(set((row[2], row[3]) for row in details))
            tx_hash = details[0][0] if details else "unknown"

            eliminations = []
            for member in original_members:
                # Find which resolved spend eliminated this member
                eliminator = None
                for res_ki, res_output in self.resolved.items():
                    if res_output == member and res_ki != ki:
                        eliminator = res_ki
                        break
                eliminations.append({
                    "output": member,
                    "eliminated_by": eliminator,
                })

            results.append({
                "key_image": ki,
                "tx_hash": tx_hash,
                "original_members": original_members,
                "eliminations": eliminations,
            })

            logger.info(f"\nEmpty ring: {ki}")
            logger.info(f"  TX: {tx_hash}")
            logger.info(f"  Original members: {original_members}")
            for e in eliminations:
                status = f"eliminated by {e['eliminated_by']}" if e['eliminated_by'] else "NOT eliminated (no match found)"
                logger.info(f"  Output {e['output']}: {status}")

        return results

    def get_unresolved_rings(self):
        return {ki: members for ki, members in self.rings.items() if len(members) > 1}

    def export_json(self, path):
        resolved_list = [
            {"key_image": ki, "real_amount": out[0], "real_output_index": out[1]}
            for ki, out in self.resolved.items()
        ]

        unresolved_sample = []
        count = 0
        for ki, members in self.rings.items():
            if len(members) > 1 and count < 1000:
                unresolved_sample.append({
                    "key_image": ki,
                    "remaining_candidates": [{"amount": m[0], "index": m[1]} for m in sorted(members)],
                    "original_size": self.original_sizes.get(ki),
                    "current_size": len(members),
                })
                count += 1

        output = {
            "summary": self.stats,
            "resolved_spends": resolved_list,
            "unresolved_sample": unresolved_sample,
        }

        with open(path, "w") as f:
            json.dump(output, f, indent=2)
        logger.info(f"Results exported to {path}")
