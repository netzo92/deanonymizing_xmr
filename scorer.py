import logging
import math
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)

GAMMA_SHAPE = 19.28
GAMMA_SCALE = 1 / 1.61
DIFFICULTY_TARGET_SECONDS = 120
DEFAULT_UNLOCK_SECONDS = 10 * DIFFICULTY_TARGET_SECONDS
RECENT_SPEND_WINDOW_SECONDS = 15 * DIFFICULTY_TARGET_SECONDS


class RingScorer:
    """
    Probabilistic ring member scoring using features derived from
    the blockchain data and ground truth from the deterministic analyzer.

    Goes beyond classical binary intersection by assigning probability
    scores to each ring member, enabling soft cascade elimination.
    """

    def __init__(self, db, analyzer):
        self.db = db
        self.analyzer = analyzer
        self.model = None
        self.training_metrics = None
        self.feature_names = [
            "output_age_rank",
            "normalized_age",
            "ring_reuse_count",
            "is_newest_member",
            "age_matches_decoy_distribution",
            "ring_size",
            "gap_to_next",
            "gap_to_prev",
            "distance_from_tx",
            "reuse_rank_in_ring",
            "is_min_reuse",
            "reuse_count_raw",
            "gap_isolation",
            "is_max_gap_member",
            "gamma_decoy_log_likelihood",
            "gamma_recent_window",
            "gamma_age_surprisal",
            "legacy_triangular_likelihood",
            "amount_bucket_progress",
            "is_legacy_amount",
            "decoy_likelihood_rank",
            "is_most_decoy_like",
            "decoy_likelihood_gap",
            "decoy_likelihood_zscore",
        ]
        self._deterministic_ki_cache = None
        self._outputs_per_block_cache = None
        self._amount_max_index_cache = None

    def _is_deterministic(self, key_image):
        """Check if a resolution is deterministic (not ML-predicted)."""
        if self._deterministic_ki_cache is None:
            cursor = self.db.conn.execute(
                "SELECT key_image FROM resolved_spends WHERE confidence = 1.0"
            )
            self._deterministic_ki_cache = set(row[0] for row in cursor)
        return key_image in self._deterministic_ki_cache

    def _outputs_per_block(self):
        if self._outputs_per_block_cache is None:
            row = self.db.conn.execute(
                "SELECT MAX(global_output_index), MAX(block_height) "
                "FROM ring_members JOIN transactions USING (tx_hash) "
                "WHERE amount = 0"
            ).fetchone()
            max_output_index, max_height = row if row else (None, None)
            if max_output_index is None or not max_height:
                self._outputs_per_block_cache = 1.0
            else:
                self._outputs_per_block_cache = max((max_output_index + 1) / max_height, 1e-9)
        return self._outputs_per_block_cache

    def _amount_max_indices(self):
        if self._amount_max_index_cache is None:
            cursor = self.db.conn.execute(
                "SELECT amount, MAX(global_output_index) FROM ring_members GROUP BY amount"
            )
            self._amount_max_index_cache = {amount: max_idx for amount, max_idx in cursor}
        return self._amount_max_index_cache

    def _gamma_decoy_features(self, member_list, amount, output_idx, tx_block_height):
        """
        Approximate Monero wallet2 gamma-picker likelihood using only the
        global output index data available in this database.
        """
        indices = [m[1] for m in member_list]
        newest_idx = max(indices)

        if amount == 0:
            outputs_per_block = self._outputs_per_block()
            estimated_tip_index = max(newest_idx, tx_block_height * outputs_per_block)
            age_blocks = max(estimated_tip_index - output_idx, 0.0) / outputs_per_block
        else:
            # Pre-RCT amount buckets do not share the RCT output distribution.
            # Fall back to relative age inside the ring for those legacy rings.
            oldest_idx = min(indices)
            age_blocks = 15 * (newest_idx - output_idx) / max(newest_idx - oldest_idx, 1)

        age_seconds = age_blocks * DIFFICULTY_TARGET_SECONDS

        if age_seconds <= RECENT_SPEND_WINDOW_SECONDS:
            recent_window = 1.0
            # wallet2 samples uniformly in this zone when gamma age falls
            # inside the default unlock period.
            log_likelihood = -math.log(RECENT_SPEND_WINDOW_SECONDS)
        else:
            recent_window = 0.0
            x = math.log(age_seconds + DEFAULT_UNLOCK_SECONDS)
            log_likelihood = (
                (GAMMA_SHAPE - 1) * math.log(max(x, 1e-12))
                - x / GAMMA_SCALE
                - GAMMA_SHAPE * math.log(GAMMA_SCALE)
                - math.lgamma(GAMMA_SHAPE)
                - math.log(age_seconds + DEFAULT_UNLOCK_SECONDS)
            )

        # Keep the feature numerically gentle for tree models and scalers.
        log_likelihood = max(min(log_likelihood, 0.0), -50.0)
        surprisal = -log_likelihood / 50.0
        return log_likelihood, recent_window, surprisal

    def _legacy_amount_features(self, amount, output_idx):
        if amount == 0:
            return 0.0, 0.0, 0.0

        max_idx = self._amount_max_indices().get(amount, output_idx)
        bucket_size = max(max_idx + 1, 1)
        progress = min(max((output_idx + 1) / bucket_size, 0.0), 1.0)

        # Legacy non-RCT decoys use a triangular distribution with mode at
        # the newest output, whose density is proportional to x / bucket_size.
        triangular_likelihood = progress
        return triangular_likelihood, progress, 1.0

    def extract_features(self, key_image, members, tx_block_height):
        """Extract feature vectors for each ring member.
        Members are (amount, global_output_index) tuples."""
        features = []
        member_list = sorted(members, key=lambda m: m[1])

        # Ring reuse counts (how many other rings each output appears in)
        reuse_counts = {}
        for member in member_list:
            count = len(self.analyzer.output_to_key_images.get(member, set()))
            reuse_counts[member] = count

        # Sorted reuse ranks (rank 0 = least reused, rank 1 = most reused)
        sorted_by_reuse = sorted(member_list, key=lambda m: reuse_counts[m])
        reuse_rank_map = {m: i / max(len(member_list) - 1, 1) for i, m in enumerate(sorted_by_reuse)}
        min_reuse = min(reuse_counts.values()) if reuse_counts else 0
        max_reuse_val = max(reuse_counts.values()) if reuse_counts else 1

        indices = [m[1] for m in member_list]
        max_idx = max(indices) if indices else 1
        min_idx = min(indices) if indices else 0
        idx_range = max_idx - min_idx if max_idx != min_idx else 1

        # Precompute gaps for isolation features
        gaps = []
        for i in range(len(member_list)):
            g_next = (member_list[i + 1][1] - member_list[i][1]) / idx_range if i < len(member_list) - 1 else 0.0
            g_prev = (member_list[i][1] - member_list[i - 1][1]) / idx_range if i > 0 else 0.0
            gaps.append((g_prev, g_next))
        max_gap = max(max(g[0], g[1]) for g in gaps) if gaps else 1.0

        decoy_likelihoods = {}
        for member in member_list:
            gamma_ll, _, _ = self._gamma_decoy_features(
                member_list, member[0], member[1], tx_block_height,
            )
            legacy_triangular, _, _ = self._legacy_amount_features(member[0], member[1])
            decoy_likelihoods[member] = legacy_triangular if member[0] != 0 else math.exp(gamma_ll)

        sorted_by_decoy_likelihood = sorted(member_list, key=lambda m: decoy_likelihoods[m])
        decoy_rank_map = {
            m: i / max(len(member_list) - 1, 1)
            for i, m in enumerate(sorted_by_decoy_likelihood)
        }
        decoy_values = np.array([decoy_likelihoods[m] for m in member_list], dtype=float)
        decoy_mean = float(np.mean(decoy_values)) if len(decoy_values) else 0.0
        decoy_std = float(np.std(decoy_values)) if len(decoy_values) else 0.0
        max_decoy_likelihood = max(decoy_likelihoods.values()) if decoy_likelihoods else 0.0

        for rank, member in enumerate(member_list):
            output_idx = member[1]

            # Feature 1: Rank position within ring (0 = oldest, 1 = newest)
            age_rank = rank / max(len(member_list) - 1, 1)

            # Feature 2: Normalized age (position relative to range of indices)
            normalized_age = (output_idx - min_idx) / idx_range

            # Feature 3: Ring reuse count (log-scaled)
            reuse = np.log1p(reuse_counts[member])

            # Feature 4: Is this the newest member in the ring?
            is_newest = 1.0 if output_idx == max_idx else 0.0

            # Feature 5: How well does this member's age match Monero's
            # decoy selection distribution? The decoy algo uses a gamma
            # distribution biased toward recent outputs. Real spends
            # tend to be recent too, but with different characteristics.
            # Score: distance from the expected decoy position.
            expected_decoy_rank = 0.75  # decoys tend to be in the newer portion
            age_dist_score = abs(age_rank - expected_decoy_rank)

            # Feature 6: Ring size (number of members)
            ring_size = float(len(member_list))

            # Feature 7: Gap to next member (0 for last member)
            if rank < len(member_list) - 1:
                gap_next = (member_list[rank + 1][1] - output_idx) / idx_range
            else:
                gap_next = 0.0

            # Feature 8: Gap from previous member (0 for first member)
            if rank > 0:
                gap_prev = (output_idx - member_list[rank - 1][1]) / idx_range
            else:
                gap_prev = 0.0

            # Feature 9: Distance from tx block height to output index
            # (proxy for how recently the output was created relative to when it was spent)
            distance_from_tx = (tx_block_height - output_idx) / max(tx_block_height, 1)

            # Feature 10: Rank by reuse count within ring (0=least reused, 1=most reused)
            reuse_rank = reuse_rank_map[member]

            # Feature 11: Is this the least-reused member? (likely real spend)
            is_min_reuse = 1.0 if reuse_counts[member] == min_reuse else 0.0

            # Feature 12: Raw reuse count (not log-scaled)
            reuse_raw = float(reuse_counts[member]) / max(max_reuse_val, 1)

            # Feature 13: Gap isolation — min of surrounding gaps (high = isolated in index space)
            g_prev_raw, g_next_raw = gaps[rank]
            gap_isolation = min(g_prev_raw, g_next_raw) / max(max_gap, 1e-9)

            # Feature 14: Is this the member with the largest surrounding gap?
            surrounding = max(g_prev_raw, g_next_raw)
            is_max_gap_member = 1.0 if surrounding >= max_gap else 0.0

            gamma_ll, gamma_recent, gamma_surprisal = self._gamma_decoy_features(
                member_list, member[0], output_idx, tx_block_height,
            )
            legacy_triangular, amount_progress, is_legacy_amount = self._legacy_amount_features(
                member[0], output_idx,
            )
            decoy_likelihood = decoy_likelihoods[member]
            decoy_rank = decoy_rank_map[member]
            is_most_decoy_like = 1.0 if decoy_likelihood == max_decoy_likelihood else 0.0
            decoy_gap = max_decoy_likelihood - decoy_likelihood
            decoy_zscore = (
                (decoy_likelihood - decoy_mean) / decoy_std
                if decoy_std > 1e-12 else 0.0
            )

            features.append({
                "output_key": member,
                "features": [age_rank, normalized_age, reuse, is_newest, age_dist_score,
                             ring_size, gap_next, gap_prev, distance_from_tx,
                             reuse_rank, is_min_reuse, reuse_raw,
                             gap_isolation, is_max_gap_member,
                             gamma_ll, gamma_recent, gamma_surprisal,
                             legacy_triangular, amount_progress, is_legacy_amount,
                             decoy_rank, is_most_decoy_like, decoy_gap,
                             decoy_zscore],
            })

        return features

    def build_training_data(self):
        """Build training data grouped by ring from deterministic resolutions only."""
        deterministic = {
            ki: out for ki, out in self.analyzer.resolved.items()
            if self._is_deterministic(ki)
        }
        if not deterministic:
            logger.warning("No deterministic resolutions available for training")
            return None

        rings_data = []

        for ki, real_output in deterministic.items():
            details = self.db.get_ring_member_details(ki)
            if not details:
                continue

            tx_hash = details[0][0]
            block_height = self.db.get_tx_block_height(tx_hash)
            if block_height is None:
                continue

            members = set((row[2], row[3]) for row in details)
            if len(members) < 2:
                continue

            feature_data = self.extract_features(ki, members, block_height)

            ring_X = [fd["features"] for fd in feature_data]
            ring_y = [1.0 if fd["output_key"] == real_output else 0.0 for fd in feature_data]
            rings_data.append({"X": ring_X, "y": ring_y})

        if not rings_data:
            return None

        logger.info(f"Built training data: {len(rings_data)} rings, "
                    f"{sum(len(r['X']) for r in rings_data)} samples")
        return rings_data

    def train(self, holdout_fraction=0.2):
        """Train a model with ring-level holdout split (no data leakage between rings)."""
        rings_data = self.build_training_data()
        self.training_metrics = None
        if rings_data is None:
            logger.warning("Cannot train: no training data")
            return False

        if len(rings_data) < 10:
            logger.warning(f"Cannot train: only {len(rings_data)} rings (need at least 10)")
            return False

        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.preprocessing import StandardScaler

            # Split at the ring level
            np.random.seed(42)
            indices = np.random.permutation(len(rings_data))
            split = int(len(rings_data) * (1 - holdout_fraction))
            train_rings = [rings_data[i] for i in indices[:split]]
            test_rings = [rings_data[i] for i in indices[split:]]

            # Flatten train set
            X_train = np.array([f for r in train_rings for f in r["X"]])
            y_train = np.array([l for r in train_rings for l in r["y"]])

            self.scaler = StandardScaler()
            X_train_scaled = self.scaler.fit_transform(X_train)

            self.model = RandomForestClassifier(
                n_estimators=500, max_depth=None, min_samples_leaf=3,
                class_weight="balanced", random_state=42, n_jobs=-1,
            )
            self.model.fit(X_train_scaled, y_train)

            # Holdout evaluation per ring
            ring_correct = 0
            ring_total = 0
            for ring in test_rings:
                X_test = np.array(ring["X"])
                y_test = np.array(ring["y"])
                X_test_scaled = self.scaler.transform(X_test)
                probas = self.model.predict_proba(X_test_scaled)[:, 1]
                predicted_idx = np.argmax(probas)
                if y_test[predicted_idx] == 1.0:
                    ring_correct += 1
                ring_total += 1

            if ring_total > 0:
                accuracy = ring_correct / ring_total * 100
                logger.info(f"Holdout validation: {ring_correct}/{ring_total} rings correct "
                            f"({accuracy:.1f}%) — split by ring, no leakage")
            else:
                accuracy = None
                logger.info("Holdout validation: no test rings")

            # Log feature importances
            feature_importances = []
            for name, imp in zip(self.feature_names, self.model.feature_importances_):
                logger.info(f"  {name}: {imp:.4f}")
                feature_importances.append({"feature": name, "importance": float(imp)})

            self.training_metrics = {
                "training_rings": len(train_rings),
                "holdout_rings": ring_total,
                "holdout_correct": ring_correct,
                "holdout_accuracy": accuracy,
                "feature_importances": sorted(
                    feature_importances,
                    key=lambda item: item["importance"],
                    reverse=True,
                ),
            }

            # Retrain on all data for actual predictions
            X_all = np.array([f for r in rings_data for f in r["X"]])
            y_all = np.array([l for r in rings_data for l in r["y"]])
            self.scaler = StandardScaler()
            X_all_scaled = self.scaler.fit_transform(X_all)
            self.model = RandomForestClassifier(
                n_estimators=500, max_depth=None, min_samples_leaf=3,
                class_weight="balanced", random_state=42, n_jobs=-1,
            )
            self.model.fit(X_all_scaled, y_all)

            return True
        except ImportError:
            logger.error("scikit-learn not installed. Run: pip install scikit-learn")
            return False

    def score_unresolved(self, confidence_threshold=0.95):
        """Score unresolved rings and return high-confidence predictions."""
        if self.model is None:
            logger.warning("Model not trained. Call train() first.")
            return []

        unresolved = self.analyzer.get_unresolved_rings()
        candidate_features = []
        candidate_rings = []
        predictions = []

        for ki, members in unresolved.items():
            details = self.db.get_ring_member_details(ki)
            if not details:
                continue

            tx_hash = details[0][0]
            block_height = self.db.get_tx_block_height(tx_hash)
            if block_height is None:
                continue

            feature_data = self.extract_features(ki, members, block_height)
            if not feature_data:
                continue

            start = len(candidate_features)
            candidate_features.extend(fd["features"] for fd in feature_data)
            candidate_rings.append({
                "key_image": ki,
                "feature_data": feature_data,
                "ring_size": len(members),
                "start": start,
                "end": len(candidate_features),
            })

        if candidate_features:
            X = np.array(candidate_features)
            X_scaled = self.scaler.transform(X)
            all_probas = self.model.predict_proba(X_scaled)[:, 1]

            for ring in candidate_rings:
                probas = all_probas[ring["start"]:ring["end"]]
                best_idx = np.argmax(probas)
                best_prob = probas[best_idx]
                best_output = ring["feature_data"][best_idx]["output_key"]

                if best_prob >= confidence_threshold:
                    predictions.append({
                        "key_image": ring["key_image"],
                        "predicted_output": best_output,
                        "confidence": float(best_prob),
                        "ring_size": ring["ring_size"],
                    })

        predictions.sort(key=lambda x: x["confidence"], reverse=True)
        logger.info(f"Scored {len(unresolved)} unresolved rings, "
                    f"{len(predictions)} above {confidence_threshold} confidence")

        # Save predictions for future verification
        for pred in predictions:
            self.db.save_prediction(
                pred["key_image"], pred["predicted_output"], pred["confidence"],
            )
        self.db.commit()

        return predictions

    def verify_predictions(self):
        """Check saved ML predictions against deterministic resolutions.

        After scanning more blocks and running analyze, some rings that
        were ML-predicted may now be deterministically resolved. This
        compares the ML guess against the ground truth.
        """
        unverified = self.db.get_unverified_predictions()
        if not unverified:
            logger.info("No unverified predictions to check")
            return {"checked": 0, "verified": 0, "correct": 0, "wrong": 0}

        deterministic = self.db.get_resolved_spends()
        checked = 0
        correct = 0
        wrong = 0
        wrong_details = []

        for pred in unverified:
            ki = pred["key_image"]
            if ki in deterministic:
                actual = deterministic[ki]
                is_correct = pred["predicted_output"] == actual
                self.db.mark_prediction_verified(ki, is_correct)
                checked += 1
                if is_correct:
                    correct += 1
                else:
                    wrong += 1
                    wrong_details.append({
                        "key_image": ki,
                        "predicted": pred["predicted_output"],
                        "actual": actual,
                        "confidence": pred["confidence"],
                    })

        self.db.commit()

        if checked:
            logger.info(f"Verified {checked} predictions: {correct} correct, {wrong} wrong")
            for d in wrong_details:
                logger.warning(
                    f"  WRONG: {d['key_image'][:16]}... predicted {d['predicted']} "
                    f"(conf={d['confidence']:.3f}) but actual was {d['actual']}"
                )
        else:
            logger.info("No predictions could be verified yet (no new deterministic resolutions)")

        return {
            "checked": checked,
            "verified": checked,
            "correct": correct,
            "wrong": wrong,
            "accuracy": f"{correct / checked * 100:.1f}%" if checked else "N/A",
        }

    def soft_cascade(self, confidence_threshold=0.95, max_passes=10):
        """
        Run probabilistic cascade: resolve high-confidence predictions
        and propagate eliminations.
        """
        if self.model is None:
            logger.warning("Model not trained")
            return {}

        total_soft_resolved = 0

        for pass_num in range(max_passes):
            predictions = self.score_unresolved(confidence_threshold)
            if not predictions:
                break

            for pred in predictions:
                ki = pred["key_image"]
                output = pred["predicted_output"]
                confidence = pred["confidence"]

                # Skip if this output is the sole member of another ring (ground truth)
                other_kis = self.analyzer.output_to_key_images.get(output, set())
                conflict = False
                for other_ki in other_kis:
                    if other_ki != ki and other_ki in self.analyzer.rings:
                        if len(self.analyzer.rings[other_ki]) == 1:
                            logger.warning(
                                f"Skipping ML prediction for {ki[:16]}... — "
                                f"output {output} is ground truth in ring {other_ki[:16]}..."
                            )
                            conflict = True
                            break
                if conflict:
                    continue

                self.analyzer.resolved[ki] = output
                if ki in self.analyzer.rings:
                    del self.analyzer.rings[ki]

                self.db.mark_resolved(ki, output, pass_num=-1, confidence=confidence)
                self.analyzer._eliminate_output(output, ki)
                total_soft_resolved += 1

            self.db.commit()

            # Check if any rings dropped to size 1 from the elimination
            newly_deterministic = set()
            for ki, members in self.analyzer.rings.items():
                if len(members) == 1:
                    newly_deterministic.add(ki)

            for ki in newly_deterministic:
                real_output = next(iter(self.analyzer.rings[ki]))
                self.analyzer.resolved[ki] = real_output
                del self.analyzer.rings[ki]
                self.db.mark_resolved(ki, real_output, pass_num=-2, confidence=1.0)
                self.analyzer._eliminate_output(real_output, ki)
                total_soft_resolved += 1

            self.db.commit()
            logger.info(f"Soft cascade pass {pass_num + 1}: "
                        f"resolved {len(predictions)} (ML) + {len(newly_deterministic)} (cascade)")

        logger.info(f"Soft cascade complete: {total_soft_resolved} additional rings resolved")
        return {
            "soft_resolved": total_soft_resolved,
            "remaining_unresolved": len(self.analyzer.get_unresolved_rings()),
        }
