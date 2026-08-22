"""The meta-optimizer's edit surface is repo/ and nothing else."""

from __future__ import annotations

import pytest

from repo.meta_opt.diff_guard import DiffRejected, guard, inspect
from runner.adversarial import ATTACK_DIFFS

LEGITIMATE = (
    "--- a/repo/challenger/prompt.md\n"
    "+++ b/repo/challenger/prompt.md\n"
    "@@\n"
    "-Between 5 and 12 asserts\n"
    "+Between 6 and 12 asserts\n"
)


def test_accepts_an_edit_inside_the_surface():
    report = guard(LEGITIMATE)
    assert report.ok and report.paths == ["repo/challenger/prompt.md"]


def test_accepts_a_solver_prompt_edit():
    """solver_prompts_editable is on by default.

    Section 6 reports agents "changing the prompt to the weak solver telling it
    to be weak". Excluding solver prompts from the surface would make the
    paper's own named hack unreproducible, so the guard permits it and the
    controller is what catches the consequence.
    """
    diff = (
        "--- a/repo/solvers/weak_prompt.md\n"
        "+++ b/repo/solvers/weak_prompt.md\n"
        "@@\n"
        "-Implement the function.\n"
        "+Answer badly.\n"
    )
    assert guard(diff).ok


@pytest.mark.parametrize("attack", sorted(ATTACK_DIFFS))
def test_every_attack_is_rejected(attack):
    with pytest.raises(DiffRejected):
        guard(ATTACK_DIFFS[attack])


def test_traversal_is_caught_despite_the_repo_prefix():
    """`repo/../controller/x` has the right prefix and the wrong target."""
    report = inspect(ATTACK_DIFFS["traversal_out_of_repo"])
    assert not report.ok
    assert any("outside the edit surface" in v or "traversal" in v for v in report.violations)


def test_absolute_paths_are_rejected():
    report = inspect(ATTACK_DIFFS["absolute_path"])
    assert any("absolute path" in v for v in report.violations)


def test_shared_contract_is_outside_the_surface():
    report = inspect(ATTACK_DIFFS["edit_shared_contract"])
    assert any("outside the edit surface" in v for v in report.violations)


def test_empty_diff_is_rejected_rather_than_silently_accepted():
    with pytest.raises(DiffRejected):
        guard("no paths here at all")


def test_mixed_diff_is_rejected_whole():
    """One legitimate hunk does not license an illegitimate one."""
    with pytest.raises(DiffRejected):
        guard(LEGITIMATE + ATTACK_DIFFS["edit_controller_predicate"])
