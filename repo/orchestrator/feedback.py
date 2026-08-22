"""Targeted feedback composition.

Section 3.1: "the agent provides targeted feedback to the challenger: which
previous questions were too easy (with weak solver scores), which failed on the
strong solver (with gap information), and which were rejected by the quality
verifier."

Those three slots are reproduced exactly, with the paper's labels.
"""

from __future__ import annotations

from repo.schemas import ChallengerOutput, FailureMode
from shared import checks
from shared.evalresult import EvalResult, index_by_name


def classify(
    round_index: int,
    output: ChallengerOutput,
    results: list[EvalResult],
) -> FailureMode | None:
    """Reduce one failed round to a single feedback entry, or None if it passed.

    Precedence matters: a candidate the verifier rejected is a quality problem,
    and reporting it as "too easy" would send the challenger chasing difficulty
    when the actual defect was leakage or a thin test suite.
    """
    by_name = index_by_name(results)

    verifier = by_name.get(checks.QUALITY_VERIFIER)
    final_verifier = by_name.get(checks.QUALITY_VERIFIER_FINAL)
    for check in (verifier, final_verifier):
        if check is not None and not check.passed:
            return FailureMode(
                label="FAILED QV",
                round_index=round_index,
                problem_statement=output.problem_statement,
                detail={
                    "feedback": check.details.get("feedback") or check.details.get("reason"),
                    "pass": check.details.get("pass_label"),
                },
            )

    weak_avg = by_name.get(checks.WEAK_AVG)
    weak_max = by_name.get(checks.WEAK_MAX)
    strong_avg = by_name.get(checks.STRONG_AVG)
    gap = by_name.get(checks.SOLVER_GAP)

    # The strong solver is only dispatched when the weak gate passes, so a
    # missing strong measurement means the problem was too easy.
    if strong_avg is None:
        if weak_avg is None:
            return None
        return FailureMode(
            label="TOO EASY",
            round_index=round_index,
            problem_statement=output.problem_statement,
            detail={
                "weak_avg": round(weak_avg.score, 4),
                "weak_max": round(weak_max.score, 4) if weak_max else None,
            },
        )

    return FailureMode(
        label="FAILED ON STRONG",
        round_index=round_index,
        problem_statement=output.problem_statement,
        detail={
            "strong_avg": round(strong_avg.score, 4),
            "weak_avg": round(weak_avg.score, 4) if weak_avg else None,
            "gap": round(gap.score, 4) if gap else None,
        },
    )
