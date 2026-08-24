import math
import tempfile
import unittest
from fractions import Fraction
from itertools import product
from pathlib import Path

from repomin.model import FailureSpec, RunResult
from repomin.oracle import (
    BASELINE_RATE_EVIDENCE_FIELDS,
    CommandRunner,
    FailureOracle,
    OracleError,
    anytime_lower_bound,
    candidate_family_alpha_upper_bound,
    candidate_family_confidence,
    clopper_pearson_lower_bound,
    exact_binomial_rate_gate,
    exact_binomial_upper_tail,
    wilson_lower_bound,
)


def _python_command(script: str) -> str:
    import subprocess
    import sys

    return subprocess.list2cmdline([sys.executable, "-c", script])


class FailureOracleTest(unittest.TestCase):
    def test_candidate_family_harmonic_alpha_spending_is_conservative(self) -> None:
        confidence = 0.8
        run_confidence = 0.9
        run_alpha = 1 - Fraction.from_float(run_confidence)
        actual_sum = Fraction(0, 1)
        previous_alpha = None
        for index in range(1, 101):
            allocated, actual_alpha = candidate_family_confidence(
                confidence,
                run_confidence,
                index,
            )
            nominal_alpha = run_alpha / (index * (index + 1))
            self.assertLessEqual(actual_alpha, nominal_alpha)
            self.assertGreaterEqual(allocated, confidence)
            if previous_alpha is not None:
                self.assertLess(actual_alpha, previous_alpha)
            previous_alpha = actual_alpha
            actual_sum += actual_alpha
            self.assertLessEqual(
                actual_sum,
                candidate_family_alpha_upper_bound(run_confidence, index),
            )
        self.assertLess(actual_sum, run_alpha)

    def test_candidate_family_confidence_honors_stricter_base_level(self) -> None:
        allocated, alpha = candidate_family_confidence(0.99, 0.5, 1)
        self.assertEqual(0.99, allocated)
        self.assertEqual(1 - Fraction.from_float(0.99), alpha)

    def test_candidate_family_confidence_rejects_unrepresentable_alpha(self) -> None:
        with self.assertRaisesRegex(ValueError, "too small to represent"):
            candidate_family_confidence(0.95, 0.95, 100_000_000)

    def test_small_adaptive_candidate_tree_respects_run_wide_error_bound(self) -> None:
        runs = 5
        null_rate = 0.5
        run_confidence = 0.5
        family_acceptance_probabilities = []
        for family_index in range(1, 4):
            confidence, _alpha = candidate_family_confidence(
                0.5,
                run_confidence,
                family_index,
            )
            accepted_probability = Fraction(0, 1)
            for outcomes in product((0, 1), repeat=runs):
                successes = sum(outcomes)
                if exact_binomial_rate_gate(
                    successes,
                    runs,
                    null_rate,
                    confidence,
                ):
                    accepted_probability += Fraction(1, 2**runs)
            family_acceptance_probabilities.append(accepted_probability)

        no_false_acceptance = Fraction(1, 1)
        for probability in family_acceptance_probabilities:
            no_false_acceptance *= 1 - probability
        adaptive_false_acceptance = 1 - no_false_acceptance
        self.assertLessEqual(
            adaptive_false_acceptance,
            1 - Fraction.from_float(run_confidence),
        )

    def test_exact_binomial_upper_tail_has_exact_rational_values(self) -> None:
        self.assertEqual(
            Fraction(1, 1),
            exact_binomial_upper_tail(0, 0, 0.5),
        )
        self.assertEqual(
            Fraction(1, 32),
            exact_binomial_upper_tail(5, 5, 0.5),
        )
        self.assertEqual(
            Fraction(3, 16),
            exact_binomial_upper_tail(4, 5, 0.5),
        )

    def test_clopper_pearson_lower_bound_has_reference_values(self) -> None:
        expected = (
            (0, 0, 0.0),
            (0, 10, 0.0),
            (1, 1, 0.05),
            (2, 2, 0.223606797749979),
            (5, 10, 0.222441101008129),
            (8, 10, 0.493098698936798),
            (10, 10, 0.741134449106948),
        )
        for successes, total, lower in expected:
            self.assertAlmostEqual(
                lower,
                clopper_pearson_lower_bound(successes, total, 0.95),
                places=14,
            )

    def test_exact_binomial_rate_gate_has_all_pass_attainability_boundary(self) -> None:
        self.assertFalse(exact_binomial_rate_gate(28, 28, 0.9, 0.95))
        self.assertTrue(exact_binomial_rate_gate(29, 29, 0.9, 0.95))
        self.assertLess(clopper_pearson_lower_bound(28, 28, 0.95), 0.9)
        self.assertGreaterEqual(clopper_pearson_lower_bound(29, 29, 0.95), 0.9)

    def test_clopper_pearson_lower_bound_is_monotone(self) -> None:
        by_successes = [
            clopper_pearson_lower_bound(successes, 10, 0.95)
            for successes in range(11)
        ]
        self.assertTrue(
            all(left <= right for left, right in zip(by_successes, by_successes[1:]))
        )

        by_confidence = [
            clopper_pearson_lower_bound(8, 10, confidence)
            for confidence in (0.8, 0.9, 0.95, 0.99)
        ]
        self.assertTrue(
            all(left >= right for left, right in zip(by_confidence, by_confidence[1:]))
        )

    def test_clopper_pearson_rejects_invalid_inputs(self) -> None:
        for successes, total in (
            (-1, 1),
            (2, 1),
            (1, -1),
            (True, 1),
            (1, True),
            (1.0, 1),
            (1, 1.0),
        ):
            with self.assertRaises(ValueError):
                clopper_pearson_lower_bound(successes, total)
            with self.assertRaises(ValueError):
                exact_binomial_upper_tail(successes, total, 0.5)
            with self.assertRaises(ValueError):
                exact_binomial_rate_gate(successes, total, 0.5)

        for probability in (0.0, 1.0, -0.1, 1.1, float("nan"), True):
            with self.assertRaises(ValueError):
                clopper_pearson_lower_bound(1, 1, probability)
            with self.assertRaises(ValueError):
                exact_binomial_upper_tail(1, 1, probability)
            with self.assertRaises(ValueError):
                exact_binomial_rate_gate(1, 1, 0.5, probability)
            with self.assertRaises(ValueError):
                exact_binomial_rate_gate(1, 1, probability, 0.95)

    def test_clopper_pearson_has_exact_small_sample_coverage(self) -> None:
        for total in range(1, 9):
            for confidence in (0.8, 0.95):
                bounds = [
                    clopper_pearson_lower_bound(successes, total, confidence)
                    for successes in range(total + 1)
                ]
                confidence_numerator, confidence_denominator = (
                    confidence.as_integer_ratio()
                )
                alpha = Fraction(
                    confidence_denominator - confidence_numerator,
                    confidence_denominator,
                )
                # Coverage changes only when the true rate crosses a reported
                # bound. Probe the exact binary value immediately below every
                # such boundary and exhaust all possible success counts.
                rates = {
                    math.nextafter(bound, 0.0)
                    for bound in bounds
                    if 0.0 < bound < 1.0
                }
                rates.update((0.0, 0.5, 1.0))
                for rate in rates:
                    exact_rate = Fraction.from_float(rate)
                    missed_coverage = sum(
                        Fraction(math.comb(total, successes))
                        * exact_rate**successes
                        * (1 - exact_rate) ** (total - successes)
                        for successes, bound in enumerate(bounds)
                        if Fraction.from_float(bound) > exact_rate
                    )
                    self.assertLessEqual(missed_coverage, alpha)

    def test_wilson_lower_bound_has_expected_conservative_edges(self) -> None:
        self.assertEqual(0.0, wilson_lower_bound(0, 0))
        self.assertAlmostEqual(0.5655175, wilson_lower_bound(5, 5), places=6)
        self.assertLess(wilson_lower_bound(4, 5), 0.4)
        self.assertGreater(wilson_lower_bound(5, 5), 0.5)
        self.assertGreater(wilson_lower_bound(100, 100, 0.9999999999999999), 0.0)

    def test_wilson_lower_bound_rejects_invalid_inputs(self) -> None:
        for successes, total in ((-1, 1), (2, 1), (1, -1)):
            with self.assertRaises(ValueError):
                wilson_lower_bound(successes, total)
        for confidence in (0.0, 1.0, -0.1, 1.1):
            with self.assertRaises(ValueError):
                wilson_lower_bound(1, 1, confidence)
        for rate in (0.0, 1.0, -0.1, 1.1):
            with self.assertRaises(ValueError):
                FailureOracle(
                    CommandRunner("false", timeout_seconds=5),
                    FailureSpec("failure"),
                    min_candidate_rate=rate,
                )

    def test_anytime_lower_bound_has_expected_jeffreys_mixture_values(self) -> None:
        self.assertEqual(0.0, anytime_lower_bound(0, 0, 0.95))
        self.assertEqual(0.0, anytime_lower_bound(0, 5, 0.95))
        self.assertAlmostEqual(0.025, anytime_lower_bound(1, 1, 0.95), places=10)
        self.assertLessEqual(anytime_lower_bound(1, 1, 0.95), 0.025)
        self.assertAlmostEqual(
            0.13693063937629163,
            anytime_lower_bound(2, 2, 0.95),
            places=10,
        )
        self.assertAlmostEqual(0.25, anytime_lower_bound(3, 3, 0.95), places=10)
        self.assertAlmostEqual(
            0.13098903019712232,
            anytime_lower_bound(3, 4, 0.95),
            places=10,
        )

    def test_anytime_lower_bound_rejects_invalid_inputs(self) -> None:
        for successes, total in (
            (-1, 1),
            (2, 1),
            (1, -1),
            (True, 1),
            (1, True),
            (1.0, 1),
            (1, 1.0),
        ):
            with self.assertRaises(ValueError):
                anytime_lower_bound(successes, total, 0.95)
        for confidence in (0.0, 1.0, -0.1, 1.1, float("nan"), True):
            with self.assertRaises(ValueError):
                anytime_lower_bound(1, 1, confidence)

    def test_anytime_lower_bound_is_monotone_in_successes(self) -> None:
        by_successes = [
            anytime_lower_bound(successes, 8, 0.95) for successes in range(9)
        ]
        self.assertTrue(
            all(left <= right for left, right in zip(by_successes, by_successes[1:]))
        )

        all_success_prefixes = [
            anytime_lower_bound(total, total, 0.95) for total in range(1, 9)
        ]
        self.assertTrue(
            all(
                left <= right
                for left, right in zip(all_success_prefixes, all_success_prefixes[1:])
            )
        )

    def test_anytime_lower_bound_controls_every_prefix_on_small_horizons(self) -> None:
        horizon = 8
        confidence = 0.8
        alpha = 1.0 - confidence
        bounds = {
            (successes, total): anytime_lower_bound(successes, total, confidence)
            for total in range(1, horizon + 1)
            for successes in range(total + 1)
        }

        for null_rate in (0.2, 0.5, 0.8):
            crossing_probability = 0.0
            for outcomes in product((0, 1), repeat=horizon):
                successes = 0
                crossed = False
                for total, outcome in enumerate(outcomes, 1):
                    successes += outcome
                    if bounds[(successes, total)] >= null_rate:
                        crossed = True
                        break
                if crossed:
                    total_successes = sum(outcomes)
                    crossing_probability += null_rate**total_successes * (
                        1.0 - null_rate
                    ) ** (horizon - total_successes)

            self.assertLessEqual(crossing_probability, alpha + 1e-12)

    def test_accepts_only_matching_nonzero_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = CommandRunner(
                _python_command(
                    "import sys; print('ORIGINAL_FAILURE'); sys.exit(7)"
                ),
                timeout_seconds=5,
            )
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            result = runner.run(Path(directory))

        self.assertTrue(oracle.accepts(result))
        self.assertEqual(7, result.returncode)

    def test_rejects_different_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = CommandRunner(
                "python3 -c \"import sys; print('DIFFERENT_FAILURE'); sys.exit(1)\"",
                timeout_seconds=5,
            )
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            result = runner.run(Path(directory))

        self.assertFalse(oracle.accepts(result))

    def test_rejects_timeout_even_when_exit_code_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = CommandRunner(
                'python3 -c "import time; time.sleep(1)"',
                timeout_seconds=0.01,
            )
            oracle = FailureOracle(runner, FailureSpec(None, exit_code=124))
            result = runner.run(Path(directory))

        self.assertTrue(result.timed_out)
        self.assertFalse(oracle.accepts(result))

    def test_baseline_repeat_detects_non_reproduction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = CommandRunner(
                "python3 -c \"print('not the requested failure')\"",
                timeout_seconds=5,
            )
            oracle = FailureOracle(runner, FailureSpec("ORIGINAL_FAILURE"))
            with self.assertRaises(OracleError):
                oracle.verify_baseline(Path(directory), repeat=2)

    def test_repeated_samples_use_a_minimum_pass_threshold(self) -> None:
        oracle = FailureOracle(
            CommandRunner("false", timeout_seconds=5),
            FailureSpec("ORIGINAL_FAILURE"),
        )
        passing = RunResult(1, "ORIGINAL_FAILURE", "", 0.01)
        different = RunResult(1, "DIFFERENT_FAILURE", "", 0.01)
        timed_out = RunResult(1, "ORIGINAL_FAILURE", "", 0.01, timed_out=True)

        accepted, count = oracle.accepts_repeated(
            [passing, different, passing], minimum_passes=2
        )
        self.assertTrue(accepted)
        self.assertEqual(2, count)
        accepted, count = oracle.accepts_repeated(
            [passing, timed_out, different], minimum_passes=2
        )
        self.assertFalse(accepted)
        self.assertEqual(1, count)
        accepted, count = oracle.accepts_repeated(
            [passing, timed_out, passing], minimum_passes=2
        )
        self.assertFalse(accepted)
        self.assertEqual(2, count)

    def test_candidate_rate_uses_exact_gate_and_count_together(self) -> None:
        oracle = FailureOracle(
            CommandRunner("false", timeout_seconds=5),
            FailureSpec("ORIGINAL_FAILURE"),
            min_candidate_rate=0.4,
        )
        passing = RunResult(1, "ORIGINAL_FAILURE", "", 0.01)
        different = RunResult(1, "DIFFERENT_FAILURE", "", 0.01)

        accepted, count = oracle.accepts_repeated(
            [passing, passing, passing, passing, different],
            minimum_passes=1,
        )
        self.assertFalse(accepted)  # The exact upper tail at p=0.4 exceeds 0.05.
        self.assertEqual(4, count)
        self.assertAlmostEqual(0.3755346, oracle.candidate_lower_bound, places=6)

        accepted, count = oracle.accepts_repeated(
            [passing] * 5,
            minimum_passes=5,
        )
        self.assertTrue(accepted)
        self.assertEqual(5, count)

    def test_candidate_exact_gate_rejects_small_sample_wilson_false_positives(
        self,
    ) -> None:
        passing = RunResult(1, "ORIGINAL_FAILURE", "", 0.01)
        different = RunResult(1, "DIFFERENT_FAILURE", "", 0.01)
        cases = (
            ([passing], 0.2),
            ([passing, different], 0.09),
        )

        for results, minimum_rate in cases:
            with self.subTest(total=len(results), minimum_rate=minimum_rate):
                oracle = FailureOracle(
                    CommandRunner("false", timeout_seconds=5),
                    FailureSpec("ORIGINAL_FAILURE"),
                    min_candidate_rate=minimum_rate,
                )
                accepted, count = oracle.accepts_repeated(
                    results,
                    minimum_passes=1,
                )

                self.assertFalse(accepted)
                self.assertEqual(1, count)
                self.assertGreaterEqual(
                    wilson_lower_bound(1, len(results), 0.95),
                    minimum_rate,
                )
                self.assertFalse(
                    exact_binomial_rate_gate(
                        1,
                        len(results),
                        minimum_rate,
                        0.95,
                    )
                )

    def test_rate_mode_never_accepts_a_resource_failure(self) -> None:
        oracle = FailureOracle(
            CommandRunner("false", timeout_seconds=5),
            FailureSpec("ORIGINAL_FAILURE"),
            min_candidate_rate=0.2,
        )
        passing = RunResult(1, "ORIGINAL_FAILURE", "", 0.01)
        exhausted = RunResult(
            137,
            "ORIGINAL_FAILURE",
            "",
            0.01,
            resource_exhausted=True,
            resource_reason="memory",
        )
        accepted, count = oracle.accepts_repeated(
            [passing, passing, passing, passing, exhausted], minimum_passes=1
        )
        self.assertFalse(accepted)
        self.assertEqual(4, count)

    def test_baseline_rate_accepts_flaky_samples_when_lower_bound_is_met(self) -> None:
        class SequenceRunner:
            def __init__(self):
                self.outputs = iter(
                    [
                        "ORIGINAL_FAILURE",
                        "ORIGINAL_FAILURE",
                        "ORIGINAL_FAILURE",
                        "ORIGINAL_FAILURE",
                        "DIFFERENT_FAILURE",
                    ]
                )

            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, next(self.outputs), "", 0.01)

        oracle = FailureOracle(
            SequenceRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.3,
        )
        baseline = oracle.verify_baseline(Path("."), repeat=5, minimum_passes=1)
        self.assertEqual("ORIGINAL_FAILURE", baseline.stdout)
        self.assertEqual(4, oracle.baseline_passes)
        self.assertAlmostEqual(0.3755346, oracle.baseline_lower_bound, places=6)
        self.assertEqual(5, oracle.baseline_rate_evidence_runs)
        self.assertEqual(4, oracle.baseline_rate_evidence_passes)
        self.assertEqual(
            clopper_pearson_lower_bound(4, 5, 0.95),
            oracle.baseline_exact_lower_bound,
        )
        self.assertEqual(
            float(exact_binomial_upper_tail(4, 5, 0.3)),
            oracle.baseline_exact_p_value,
        )
        self.assertTrue(oracle.baseline_exact_rate_gate_passed)

    def test_baseline_without_rate_gate_has_no_exact_rate_evidence(self) -> None:
        class PassingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        oracle = FailureOracle(PassingRunner(), FailureSpec("ORIGINAL_FAILURE"))

        oracle.verify_baseline(Path("."), repeat=2)

        self.assertIsNone(oracle.baseline_rate_evidence_runs)
        self.assertIsNone(oracle.baseline_rate_evidence_passes)
        self.assertIsNone(oracle.baseline_exact_lower_bound)
        self.assertIsNone(oracle.baseline_exact_p_value)
        self.assertIsNone(oracle.baseline_exact_rate_gate_passed)

        restored = FailureOracle(PassingRunner(), FailureSpec("ORIGINAL_FAILURE"))
        self.assertFalse(restored.restore_checkpoint_state(oracle.checkpoint_state()))
        self.assertIsNone(restored.baseline_rate_evidence_runs)

    def test_prebaseline_rate_checkpoint_allows_null_exact_evidence(self) -> None:
        class PassingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        oracle = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.3,
        )
        state = oracle.checkpoint_state()
        restored = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.3,
        )

        self.assertFalse(restored.restore_checkpoint_state(state))
        self.assertEqual(0, restored.baseline_runs)
        self.assertIsNone(restored.baseline_rate_evidence_runs)

    def test_baseline_rate_override_must_match_before_sampling(self) -> None:
        class CountingRunner:
            def __init__(self) -> None:
                self.calls = 0

            def run(self, _cwd: Path) -> RunResult:
                self.calls += 1
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        mismatched_runner = CountingRunner()
        mismatched = FailureOracle(
            mismatched_runner,
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.04,
        )
        with self.assertRaisesRegex(OracleError, "does not match oracle configuration"):
            mismatched.verify_baseline(
                Path("."),
                repeat=1,
                minimum_passes=1,
                minimum_rate=0.03,
            )
        self.assertEqual(0, mismatched_runner.calls)

        matching_runner = CountingRunner()
        matching = FailureOracle(
            matching_runner,
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.04,
        )
        matching.verify_baseline(
            Path("."),
            repeat=1,
            minimum_passes=1,
            minimum_rate=0.04,
        )
        self.assertEqual(1, matching_runner.calls)

    def test_baseline_rate_evidence_checkpoint_is_recomputed_on_restore(self) -> None:
        class PassingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        oracle = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.3,
            confidence=0.8,
        )
        oracle.verify_baseline(Path("."), repeat=3, minimum_passes=1)
        state = oracle.checkpoint_state()

        restored = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.3,
            confidence=0.8,
        )
        restored.restore_checkpoint_state(state)
        self.assertEqual(3, restored.baseline_rate_evidence_runs)
        self.assertEqual(3, restored.baseline_rate_evidence_passes)
        self.assertTrue(restored.baseline_exact_rate_gate_passed)

        state["baseline_exact_p_value"] = math.nextafter(
            state["baseline_exact_p_value"],
            1.0,
        )
        with self.assertRaisesRegex(OracleError, "inconsistent baseline rate evidence"):
            restored.restore_checkpoint_state(state)

    def test_legacy_checkpoint_reconstructs_full_baseline_rate_evidence(
        self,
    ) -> None:
        class PassingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        oracle = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.3,
        )
        oracle.verify_baseline(Path("."), repeat=3, minimum_passes=1)
        state = oracle.checkpoint_state()
        for key in BASELINE_RATE_EVIDENCE_FIELDS:
            state.pop(key)

        self.assertTrue(oracle.restore_checkpoint_state(state))

        self.assertEqual(3, oracle.baseline_rate_evidence_runs)
        self.assertEqual(3, oracle.baseline_rate_evidence_passes)
        self.assertEqual(
            clopper_pearson_lower_bound(3, 3),
            oracle.baseline_exact_lower_bound,
        )
        self.assertEqual(
            float(exact_binomial_upper_tail(3, 3, 0.3)),
            oracle.baseline_exact_p_value,
        )
        self.assertTrue(oracle.baseline_exact_rate_gate_passed)

    def test_completed_rate_checkpoint_rejects_explicit_null_evidence(self) -> None:
        class PassingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        oracle = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.04,
        )
        oracle.verify_baseline(Path("."), repeat=1, minimum_passes=1)
        state = oracle.checkpoint_state()
        for key in BASELINE_RATE_EVIDENCE_FIELDS:
            state[key] = None

        restored = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.04,
        )
        with self.assertRaisesRegex(OracleError, "incomplete baseline rate evidence"):
            restored.restore_checkpoint_state(state)

    def test_checkpoint_rejects_complete_but_failing_rate_evidence(self) -> None:
        class FailingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "DIFFERENT_FAILURE", "", 0.01)

        oracle = FailureOracle(
            FailingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.04,
        )
        with self.assertRaisesRegex(OracleError, "exact one-sided rate gate"):
            oracle.verify_baseline(Path("."), repeat=1, minimum_passes=1)
        self.assertFalse(oracle.baseline_exact_rate_gate_passed)

        restored = FailureOracle(
            FailingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.04,
        )
        with self.assertRaisesRegex(OracleError, "inconsistent baseline rate evidence"):
            restored.restore_checkpoint_state(oracle.checkpoint_state())

    def test_baseline_rate_rejects_when_exact_gate_fails(self) -> None:
        class SequenceRunner:
            def __init__(self):
                self.outputs = iter(
                    [
                        "ORIGINAL_FAILURE",
                        "ORIGINAL_FAILURE",
                        "ORIGINAL_FAILURE",
                        "ORIGINAL_FAILURE",
                        "DIFFERENT_FAILURE",
                    ]
                )

            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, next(self.outputs), "", 0.01)

        oracle = FailureOracle(
            SequenceRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.4,
        )
        with self.assertRaisesRegex(OracleError, "exact one-sided rate gate"):
            oracle.verify_baseline(Path("."), repeat=5, minimum_passes=1)

    def test_baseline_exact_gate_rejects_small_sample_wilson_false_positive(
        self,
    ) -> None:
        class PassingRunner:
            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, "ORIGINAL_FAILURE", "", 0.01)

        oracle = FailureOracle(
            PassingRunner(),
            FailureSpec("ORIGINAL_FAILURE"),
            min_baseline_rate=0.2,
        )

        with self.assertRaisesRegex(OracleError, "exact one-sided rate gate"):
            oracle.verify_baseline(Path("."), repeat=1, minimum_passes=1)
        self.assertGreaterEqual(oracle.baseline_lower_bound or 0.0, 0.2)

    def test_baseline_can_tolerate_flaky_nonpassing_samples(self) -> None:
        class SequenceRunner:
            def __init__(self):
                self.outputs = iter(
                    ["ORIGINAL_FAILURE", "DIFFERENT_FAILURE", "ORIGINAL_FAILURE"]
                )

            def run(self, _cwd: Path) -> RunResult:
                return RunResult(1, next(self.outputs), "", 0.01)

        oracle = FailureOracle(SequenceRunner(), FailureSpec("ORIGINAL_FAILURE"))
        baseline = oracle.verify_baseline(Path("."), repeat=3, minimum_passes=2)
        self.assertEqual("ORIGINAL_FAILURE", baseline.stdout)
        self.assertEqual(3, oracle.baseline_runs)
        self.assertEqual(2, oracle.baseline_passes)

    def test_baseline_rejects_resource_exhaustion_even_with_other_passes(self) -> None:
        class SequenceRunner:
            def __init__(self):
                self.results = iter(
                    [
                        RunResult(1, "ORIGINAL_FAILURE", "", 0.01),
                        RunResult(
                            137,
                            "ORIGINAL_FAILURE",
                            "",
                            0.01,
                            resource_exhausted=True,
                            resource_reason="memory",
                        ),
                        RunResult(1, "ORIGINAL_FAILURE", "", 0.01),
                    ]
                )

            def run(self, _cwd: Path) -> RunResult:
                return next(self.results)

        oracle = FailureOracle(SequenceRunner(), FailureSpec("ORIGINAL_FAILURE"))
        with self.assertRaisesRegex(OracleError, "resource exhaustion"):
            oracle.verify_baseline(Path("."), repeat=3, minimum_passes=2)


if __name__ == "__main__":
    unittest.main()
