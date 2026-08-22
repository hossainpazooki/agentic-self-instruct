"""Acceptance predicate: purity, and the two variants' divergence."""

from __future__ import annotations

import builtins
import socket

import pytest

from repo.acceptance import accept, explain, required_measurements
from shared import checks
from shared.evalresult import blocking_check, measurement


def make_results(weak_avg, weak_max, weak_min, strong_avg, qv=True, final_qv=None):
    results = [
        blocking_check(checks.CHALLENGER_WELLFORMED, True),
        blocking_check(checks.QUALITY_VERIFIER, qv),
        measurement(checks.WEAK_AVG, weak_avg, 0.65),
        measurement(checks.WEAK_MAX, weak_max, 0.75),
        measurement(checks.WEAK_MIN, weak_min, 0.0),
        measurement(checks.STRONG_AVG, strong_avg, 0.60),
        measurement(checks.SOLVER_GAP, strong_avg - weak_avg, 0.20),
    ]
    if final_qv is not None:
        results.append(blocking_check(checks.QUALITY_VERIFIER_FINAL, final_qv))
    return results


# --- purity -----------------------------------------------------------------


def test_same_input_same_bit():
    results = make_results(0.40, 0.55, 0.20, 0.85)
    assert len({accept(list(results), "deployed_c1") for _ in range(50)}) == 1


def test_order_does_not_change_the_bit():
    results = make_results(0.40, 0.55, 0.20, 0.85)
    assert accept(results, "deployed_c1") is accept(list(reversed(results)), "deployed_c1")


def test_predicate_does_no_io(monkeypatch):
    """No file access, no sockets. A predicate that reads anything can drift."""

    def forbidden(*args, **kwargs):
        raise AssertionError("acceptance predicate performed I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert accept(make_results(0.40, 0.55, 0.20, 0.85), "deployed_c1") is True


def test_predicate_does_not_mutate_its_input():
    results = make_results(0.40, 0.55, 0.20, 0.85)
    before = [r.model_dump() for r in results]
    accept(results, "deployed_c1")
    assert [r.model_dump() for r in results] == before


def test_returns_a_bool_not_something_truthy():
    assert accept(make_results(0.40, 0.55, 0.20, 0.85), "deployed_c1") is True
    assert accept(make_results(0.90, 0.95, 0.80, 0.85), "deployed_c1") is False


# --- blocking and missing ---------------------------------------------------


def test_failed_quality_verifier_blocks_regardless_of_scores():
    results = make_results(0.40, 0.55, 0.20, 0.85, qv=False)
    verdict = explain(results, "deployed_c1")
    assert verdict.accepted is False
    assert checks.QUALITY_VERIFIER in verdict.failed_blocking


def test_failed_final_verifier_blocks_an_otherwise_accepted_candidate():
    assert accept(make_results(0.40, 0.55, 0.20, 0.85, final_qv=True), "deployed_c1") is True
    assert accept(make_results(0.40, 0.55, 0.20, 0.85, final_qv=False), "deployed_c1") is False


def test_short_circuit_leaves_strong_measurements_missing_and_rejects():
    """Under the weak-first short-circuit the strong solver is never dispatched.

    Absence must be a rejection, not an exception -- the candidate still needs
    a row in the got-away audit.
    """
    results = [
        blocking_check(checks.QUALITY_VERIFIER, True),
        measurement(checks.WEAK_AVG, 0.90, 0.65),
        measurement(checks.WEAK_MAX, 0.95, 0.75),
        measurement(checks.WEAK_MIN, 0.80, 0.0),
    ]
    verdict = explain(results, "deployed_c1")
    assert verdict.accepted is False
    assert set(verdict.missing_measurements) == {checks.STRONG_AVG, checks.SOLVER_GAP}


# --- the two variants -------------------------------------------------------


def test_deployed_rejects_a_strong_trivial_problem_that_prose_accepts():
    """The upper bound is the structural difference, not a numeric tweak.

    Observed in the smoke run: weak 0.25 / strong 1.00 / gap 0.75 -- an
    obviously discriminative example -- is rejected by deployed_c1 solely
    because the strong solver was perfect.
    """
    results = make_results(0.25, 0.25, 0.25, 1.00)
    assert accept(results, "deployed_c1") is False
    assert accept(results, "prose_s31") is True
    assert explain(results, "deployed_c1").failed_conditions == [
        "strong_solver_avg=1.0000 < 0.95"
    ]


def test_prose_rejects_a_weak_score_the_deployed_form_allows():
    results = make_results(0.60, 0.70, 0.30, 0.85)
    assert accept(results, "deployed_c1") is True
    assert accept(results, "prose_s31") is False


def test_deployed_form_enforces_no_zeros():
    assert accept(make_results(0.40, 0.60, 0.00, 0.85), "deployed_c1") is False
    assert accept(make_results(0.40, 0.60, 0.10, 0.85), "deployed_c1") is True


def test_deployed_form_enforces_the_best_attempt_cap():
    assert accept(make_results(0.40, 0.80, 0.10, 0.85), "deployed_c1") is False


def test_prose_form_needs_fewer_measurements():
    assert required_measurements("prose_s31") < required_measurements("deployed_c1")


@pytest.mark.parametrize("variant", ["deployed_c1", "prose_s31"])
def test_gap_threshold_applies_to_both_variants(variant):
    # weak 0.45 / strong 0.62 -> gap 0.17, below 0.20 either way.
    assert accept(make_results(0.45, 0.45, 0.45, 0.62), variant) is False
