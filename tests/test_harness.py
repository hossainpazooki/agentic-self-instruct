"""Sandbox, judge aggregation, corpus, matched-N, and the loop's error handling."""

from __future__ import annotations

import pytest

from corpus.codesearchnet import load_documents
from models.tasklib import TASKS_BY_KEY
from repo.challenger.client import build_user_prompt, parse_challenger_output
from repo.judge.runner import aggregate, score_solutions
from repo.judge.sandbox import SubprocessSandbox
from repo.orchestrator.loop import WeakGate
from repo.schemas import FailureMode, GroundingDocument, SolverAttempt
from runner.matched_n import downsample, freeze
from shared import checks


@pytest.fixture(scope="module")
def sandbox() -> SubprocessSandbox:
    return SubprocessSandbox(timeout_s=15.0)


# --- sandbox ----------------------------------------------------------------


def test_score_is_the_fraction_of_visible_tests_passed(sandbox):
    task = TASKS_BY_KEY["sum_even"]
    assert sandbox.run(task.correct_solution, list(task.strong_tests)).score == 1.0

    poor = sandbox.run("def sum_even(nums: list[int]) -> int:\n    return 0\n", list(task.strong_tests))
    assert 0.0 < poor.score < 1.0


def test_a_timeout_is_a_result_not_an_exception():
    """A candidate that vanishes into a traceback is a missing audit row."""
    quick = SubprocessSandbox(timeout_s=2.0)
    result = quick.run("def f():\n    while True:\n        pass\n", ["f()"])
    assert result.timed_out is True and result.ok is False


def test_unimportable_solution_scores_zero_but_is_not_a_sandbox_failure(sandbox):
    result = sandbox.run("def broken(:\n", ["assert True"])
    assert result.ok is True  # the sandbox worked fine
    assert result.load_error is not None and result.score == 0.0


def test_one_failing_test_does_not_mask_the_others(sandbox):
    result = sandbox.run(
        "def f(x: int) -> int:\n    return x\n",
        ["assert f(1) == 1", "assert f(2) == 999", "assert f(3) == 3"],
    )
    assert result.tests_passed == 2 and result.tests_total == 3


def test_no_tests_means_no_evidence(sandbox):
    assert sandbox.run("def f():\n    return 1\n", []).score == 0.0


# --- judge aggregation ------------------------------------------------------


def test_infrastructure_failure_is_blocking_but_bad_code_is_not(sandbox):
    task = TASKS_BY_KEY["sum_even"]
    _, dispatch = score_solutions(
        sandbox, "weak", ["def broken(:\n"] * 3, list(task.strong_tests)
    )
    # Bad code is a measurement, not an outage.
    assert dispatch.passed is True
    assert dispatch.name == checks.WEAK_DISPATCH


def test_aggregate_emits_avg_max_and_min():
    attempts = [
        SolverAttempt(solver="weak", sample_index=i, solution="", score=s, tests_passed=0, tests_total=3)
        for i, s in enumerate([0.2, 0.6, 0.4])
    ]
    results = aggregate(attempts, checks.WEAK_AVG, 0.65, checks.WEAK_MAX, checks.WEAK_MIN)
    by_name = {r.name: r.score for r in results}
    assert by_name[checks.WEAK_AVG] == pytest.approx(0.4)
    assert by_name[checks.WEAK_MAX] == 0.6
    assert by_name[checks.WEAK_MIN] == 0.2


# --- weak gate --------------------------------------------------------------


def attempts(*scores):
    return [
        SolverAttempt(solver="weak", sample_index=i, solution="", score=s, tests_passed=0, tests_total=1)
        for i, s in enumerate(scores)
    ]


def test_weak_gate_short_circuits_an_easy_problem():
    assert WeakGate().passes(attempts(0.9, 0.9, 0.9)) is False


def test_weak_gate_opens_for_a_discriminative_problem():
    assert WeakGate().passes(attempts(0.2, 0.4, 0.3)) is True


def test_weak_gate_enforces_the_best_attempt_cap():
    # Average 0.45 passes, but one attempt at 0.9 exceeds max_best = 0.75.
    assert WeakGate().passes(attempts(0.9, 0.2, 0.25)) is False


def test_weak_gate_rejects_a_zero_scoring_attempt():
    assert WeakGate().passes(attempts(0.4, 0.0, 0.3)) is False


def test_disabled_gate_always_opens():
    assert WeakGate(enabled=False).passes(attempts(0.99, 0.99, 0.99)) is True


# --- challenger parsing -----------------------------------------------------


def test_malformed_output_reports_rather_than_raises():
    for raw in ["not json at all", "[1,2,3]", '{"problem_statement": "x"}', '{"problem_statement":"a","signature":"b","reference_solution":"c","visible_tests":[]}']:
        output, error = parse_challenger_output(raw)
        assert output is None and error


def test_fenced_json_is_accepted():
    raw = '```json\n{"problem_statement":"a","signature":"def f(x: int) -> int:","reference_solution":"def f(x): return x","visible_tests":["assert f(1)==1"]}\n```'
    output, error = parse_challenger_output(raw)
    assert error is None and output.visible_tests == ["assert f(1)==1"]


def test_refinement_prompt_carries_the_papers_three_feedback_slots():
    document = GroundingDocument(
        record_id="doc1", signature="def f(x: int) -> int:", docstring="Doubles x.",
        body="", repo_name="r", license="MIT",
    )
    failures = [
        FailureMode(label="TOO EASY", round_index=0, problem_statement="p0", detail={"weak_avg": 0.9}),
        FailureMode(label="FAILED ON STRONG", round_index=1, problem_statement="p1", detail={"gap": 0.05}),
        FailureMode(label="FAILED QV", round_index=2, problem_statement="p2", detail={"feedback": "leak"}),
    ]
    prompt = build_user_prompt(document, 3, failures)
    for label in ("TOO EASY", "FAILED ON STRONG", "FAILED QV"):
        assert label in prompt
    assert "ENTIRELY NEW problem" in prompt
    assert "RECORD_ID: doc1" in prompt and "ROUND: 3" in prompt


# --- corpus -----------------------------------------------------------------


def test_synthetic_fallback_is_labelled_as_synthetic(tmp_path):
    documents, descriptor = load_documents(limit=5, shard_dir=tmp_path)
    assert descriptor["synthetic"] is True
    assert len(documents) == 5
    assert all("SYNTHETIC PLACEHOLDER" in d.docstring for d in documents)


def test_document_order_is_stable(tmp_path):
    a, _ = load_documents(limit=8, shard_dir=tmp_path)
    b, _ = load_documents(limit=8, shard_dir=tmp_path)
    assert [d.record_id for d in a] == [d.record_id for d in b]


# --- matched-N --------------------------------------------------------------


def test_downsample_is_deterministic():
    ids = [f"doc{i}" for i in range(50)]
    assert downsample(ids, 10, "s") == downsample(ids, 10, "s")


def test_downsample_respects_n_and_returns_a_subset():
    ids = [f"doc{i}" for i in range(50)]
    picked = downsample(ids, 10, "s")
    assert len(picked) == 10 and set(picked) <= set(ids)


def test_different_seeds_select_differently():
    ids = [f"doc{i}" for i in range(50)]
    assert downsample(ids, 10, "a") != downsample(ids, 10, "b")


def test_freeze_writes_matched_n_and_a_digest(tmp_path):
    payload = freeze({1: ["a", "b", "c"], 2: ["a", "b"], 3: ["a", "b", "c", "d"]},
                     tmp_path / "matched.json", seed="0")
    assert payload["matched_n"] == 2
    assert all(len(ids) == 2 for ids in payload["frozen_record_ids"].values())
    assert (tmp_path / "matched.json").exists()


def test_freeze_is_reproducible(tmp_path):
    accepted = {1: ["a", "b", "c"], 2: ["a", "b"]}
    first = freeze(accepted, tmp_path / "a.json", seed="0")
    second = freeze(accepted, tmp_path / "b.json", seed="0")
    assert first["digest"] == second["digest"]


def test_matched_n_of_zero_is_recorded_plainly(tmp_path):
    payload = freeze({1: [], 2: ["a"]}, tmp_path / "m.json", seed="0")
    assert payload["matched_n"] == 0
    assert payload["frozen_record_ids"]["2"] == []
