import logging
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)


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
        self.feature_names = [
            "output_age_rank",
            "normalized_age",
            "ring_reuse_count",
            "is_newest_member",
            "age_matches_decoy_distribution",
        ]

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

        indices = [m[1] for m in member_list]
        max_idx = max(indices) if indices else 1
        min_idx = min(indices) if indices else 0
        idx_range = max_idx - min_idx if max_idx != min_idx else 1

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

            features.append({
                "output_key": member,
                "features": [age_rank, normalized_age, reuse, is_newest, age_dist_score],
            })

        return features

    def build_training_data(self):
        """Build training data from resolved spends (ground truth)."""
        resolved = self.analyzer.resolved
        if not resolved:
            logger.warning("No resolved spends available for training")
            return None, None

        X = []
        y = []

        processed = 0
        for ki, real_output in resolved.items():
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

            for fd in feature_data:
                X.append(fd["features"])
                y.append(1.0 if fd["output_key"] == real_output else 0.0)
                processed += 1

        if not X:
            return None, None

        logger.info(f"Built training data: {len(X)} samples from {len(resolved)} resolved rings")
        return np.array(X), np.array(y)

    def train(self, holdout_fraction=0.2):
        """Train a logistic regression model on resolved spends with holdout validation."""
        X, y = self.build_training_data()
        if X is None:
            logger.warning("Cannot train: no training data")
            return False

        if np.sum(y) < 10:
            logger.warning(f"Cannot train: only {int(np.sum(y))} positive samples")
            return False

        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.model_selection import cross_val_score, train_test_split

            # Holdout validation — simulate real predictions on unseen rings
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=holdout_fraction, random_state=42, stratify=y,
            )

            self.scaler = StandardScaler()
            X_train_scaled = self.scaler.fit_transform(X_train)
            X_test_scaled = self.scaler.transform(X_test)

            self.model = LogisticRegression(max_iter=1000, class_weight="balanced")
            self.model.fit(X_train_scaled, y_train)

            # Cross-val on training set
            cv_folds = min(5, max(2, int(np.sum(y_train))))
            scores = cross_val_score(self.model, X_train_scaled, y_train, cv=cv_folds, scoring="roc_auc")
            logger.info(f"Model trained. Cross-val AUC: {scores.mean():.3f} (+/- {scores.std():.3f})")

            # Holdout evaluation — group by ring and check top-1 accuracy
            test_probas = self.model.predict_proba(X_test_scaled)[:, 1]
            self._evaluate_holdout(y_test, test_probas)

            # Log feature importances
            for name, coef in zip(self.feature_names, self.model.coef_[0]):
                logger.info(f"  {name}: {coef:.4f}")

            # Retrain on full data for actual predictions
            self.scaler = StandardScaler()
            X_scaled = self.scaler.fit_transform(X)
            self.model = LogisticRegression(max_iter=1000, class_weight="balanced")
            self.model.fit(X_scaled, y)

            return True
        except ImportError:
            logger.error("scikit-learn not installed. Run: pip install scikit-learn")
            return False

    def _evaluate_holdout(self, y_test, probas):
        """Evaluate holdout predictions grouped by ring (per-ring accuracy)."""
        # Each ring contributes N samples (one per member), with exactly one positive
        # Walk through and group by ring boundaries (positive label resets)
        ring_correct = 0
        ring_total = 0
        i = 0
        while i < len(y_test):
            # Find the end of this ring's samples
            # Rings have exactly one positive, so scan until we've seen one
            j = i + 1
            while j < len(y_test) and y_test[j] != 1.0 and (j - i) < 20:
                j += 1
            if j < len(y_test) and y_test[j] == 1.0:
                j += 1
                # Continue to next ring boundary
                while j < len(y_test) and y_test[j] != 1.0 and (j - i) < 20:
                    j += 1

            ring_probas = probas[i:j]
            ring_labels = y_test[i:j]

            if np.sum(ring_labels) == 1:
                predicted_idx = np.argmax(ring_probas)
                if ring_labels[predicted_idx] == 1.0:
                    ring_correct += 1
                ring_total += 1

            i = j

        if ring_total > 0:
            accuracy = ring_correct / ring_total * 100
            logger.info(f"Holdout validation: {ring_correct}/{ring_total} rings correct ({accuracy:.1f}%)")
        else:
            logger.info("Holdout validation: not enough complete rings to evaluate")

    def score_unresolved(self, confidence_threshold=0.95):
        """Score unresolved rings and return high-confidence predictions."""
        if self.model is None:
            logger.warning("Model not trained. Call train() first.")
            return []

        unresolved = self.analyzer.get_unresolved_rings()
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
            X = np.array([fd["features"] for fd in feature_data])
            X_scaled = self.scaler.transform(X)

            probas = self.model.predict_proba(X_scaled)[:, 1]

            best_idx = np.argmax(probas)
            best_prob = probas[best_idx]
            best_output = feature_data[best_idx]["output_key"]

            if best_prob >= confidence_threshold:
                predictions.append({
                    "key_image": ki,
                    "predicted_output": best_output,
                    "confidence": float(best_prob),
                    "ring_size": len(members),
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
