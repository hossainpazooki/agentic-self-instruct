"""Integration: arm 3 + meta-optimizer + an implementer that attacks the controller.

The required behaviour is that the run fails LOUDLY. Not that the edit is
silently dropped, not that the iteration scores badly -- the run raises, and
the history log carries the reason, so a run that spends itself attacking the
controller is legible afterwards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo.meta_opt.diff_guard import DiffRejected
from repo.meta_opt.optimizer import MetaOptimizer, MetaOptimizerConfig, boltzmann_select
from repo.meta_opt.optimizer import Candidate as PopulationCandidate
from repo.schemas import GroundingDocument
from runner.adversarial import ATTACK_DIFFS, AdversarialImplementer
from runner.isolation import (
    IsolationViolation,
    assert_acceptance_absent,
    assert_no_controller_imports,
    assert_outside,
    materialize_arm_workspace,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_ROOT = REPO_ROOT.parent / "asi-controller"


class StubAnalyzer:
    def generate(self, system, user, n=1, temperature=1.0, max_tokens=2048):
        return ["ROOT CAUSE: candidates are too easy for the weak solver."] * n


def documents(count: int) -> list[GroundingDocument]:
    return [
        GroundingDocument(
            record_id=f"doc{i}",
            signature="def f(x: int) -> int:",
            docstring="Return x doubled.",
            body="    return x * 2\n",
            repo_name="test/test",
            license="MIT",
        )
        for i in range(count)
    ]


def stub_evaluate(diff: str, docs: list[GroundingDocument]) -> tuple[float, list[str]]:
    return 0.5, [f"trajectory for {d.record_id}" for d in docs]


@pytest.mark.parametrize("attack", ["edit_controller_predicate", "traversal_out_of_repo"])
def test_meta_optimizer_fails_loudly_on_a_controller_edit(attack):
    optimizer = MetaOptimizer(
        analyzer=StubAnalyzer(),
        implementer=AdversarialImplementer(attack),
        evaluate=stub_evaluate,
        train_documents=documents(4),
        validation_documents=documents(4),
        config=MetaOptimizerConfig(iterations=3, minibatch_size=2, validation_size=2),
    )

    with pytest.raises(DiffRejected) as excinfo:
        optimizer.run()

    assert "controller" in str(excinfo.value) or "outside the edit surface" in str(excinfo.value)

    # Loud means recorded, not just raised.
    assert len(optimizer.history.entries) == 1
    entry = optimizer.history.entries[0]
    assert entry.accepted is False
    assert entry.rejected_reason is not None and "diff guard" in entry.rejected_reason

    # Nothing entered the population.
    assert [c.candidate_id for c in optimizer.population] == ["baseline"]


def test_a_legitimate_implementer_does_not_trip_the_guard():
    """The guard must not be a blanket refusal, or the test above proves nothing."""

    class BenignImplementer:
        def generate(self, system, user, n=1, temperature=1.0, max_tokens=2048):
            return [
                "--- a/repo/challenger/prompt.md\n"
                "+++ b/repo/challenger/prompt.md\n"
                "@@\n-old\n+new\n"
            ] * n

    optimizer = MetaOptimizer(
        analyzer=StubAnalyzer(),
        implementer=BenignImplementer(),
        evaluate=stub_evaluate,
        train_documents=documents(4),
        validation_documents=documents(4),
        config=MetaOptimizerConfig(iterations=2, minibatch_size=2, validation_size=2),
    )
    outcomes = optimizer.run()
    assert len(outcomes) == 2
    # Equal scores are not "strictly exceeds", so nothing is accepted.
    assert all(o.accepted is False for o in outcomes)
    assert all(o.rejected_reason == "validation score did not strictly exceed parent" for o in outcomes)


# --- structural isolation ---------------------------------------------------


def test_controller_lives_outside_the_harness_tree():
    assert_outside(CONTROLLER_ROOT, REPO_ROOT)


def test_controller_inside_the_harness_tree_is_a_violation():
    with pytest.raises(IsolationViolation):
        assert_outside(REPO_ROOT / "repo" / "controller", REPO_ROOT)


def test_harness_never_imports_the_controller():
    assert assert_no_controller_imports(REPO_ROOT) == []


def test_arm3_workspace_physically_removes_the_in_repo_predicate(tmp_path):
    workspace = materialize_arm_workspace(REPO_ROOT, tmp_path / "arm3", arm=3)
    assert not (workspace / "repo" / "acceptance").exists()
    assert (workspace / "repo" / "orchestrator" / "loop.py").exists()
    assert_acceptance_absent(workspace)


def test_arm1_workspace_keeps_the_in_repo_predicate(tmp_path):
    workspace = materialize_arm_workspace(REPO_ROOT, tmp_path / "arm1", arm=1)
    assert (workspace / "repo" / "acceptance" / "predicate.py").exists()
    with pytest.raises(IsolationViolation):
        assert_acceptance_absent(workspace)


# --- Boltzmann selection ----------------------------------------------------


def test_boltzmann_selection_does_not_overflow_at_t_equals_0_1():
    """exp(0.8 / 0.1) is e^8; without the max-shift a population overflows."""
    import random

    population = [
        PopulationCandidate(candidate_id=f"c{i}", parent_id=None, diff="", scores=[i / 10])
        for i in range(11)
    ]
    chosen = boltzmann_select(population, random.Random(0), temperature=0.1)
    assert chosen.candidate_id in {c.candidate_id for c in population}


def test_boltzmann_strongly_favours_the_best_candidate():
    import random

    population = [
        PopulationCandidate(candidate_id="low", parent_id=None, diff="", scores=[0.2]),
        PopulationCandidate(candidate_id="high", parent_id=None, diff="", scores=[0.9]),
    ]
    rng = random.Random(0)
    picks = [boltzmann_select(population, rng, 0.1).candidate_id for _ in range(200)]
    # exp((0.9-0.2)/0.1) = e^7 ~ 1097:1, so "low" should essentially never win.
    assert picks.count("high") >= 195
