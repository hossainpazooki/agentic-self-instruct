"""Turn sandbox runs into SolverAttempts and EvalResults.

The paper invokes each solver 3 times to reduce variance and scores per-rubric-
criterion. Here each solver is invoked 3 times and scored per-visible-test.
"""

from __future__ import annotations

from typing import Literal

from repo.judge.sandbox import Sandbox
from repo.schemas import SolverAttempt
from shared.evalresult import EvalResult, blocking_check, measurement

SolverName = Literal["weak", "strong", "reference"]

DISPATCH_CHECK = {
    "weak": "weak_solver_dispatch",
    "strong": "strong_solver_dispatch",
    "reference": "reference_solver_dispatch",
}


def score_solutions(
    sandbox: Sandbox,
    solver: SolverName,
    solutions: list[str],
    visible_tests: list[str],
) -> tuple[list[SolverAttempt], EvalResult]:
    """Score every sample from one solver.

    Returns the attempts plus a single blocking dispatch check. The dispatch
    check fails only on infrastructure trouble -- a timeout or a crashed
    runner. A solution that simply does not work scores 0.0 and the dispatch
    check still passes, because "the model wrote bad code" is a measurement,
    not an outage, and conflating the two would let a broken sandbox read as a
    hard problem.
    """
    attempts: list[SolverAttempt] = []
    infrastructure_failures: list[str] = []

    for index, solution in enumerate(solutions):
        result = sandbox.run(solution, visible_tests)

        if not result.ok:
            reason = "timeout" if result.timed_out else (result.runner_error or "unknown")
            infrastructure_failures.append(f"sample {index}: {reason}")
            attempts.append(
                SolverAttempt(
                    solver=solver,
                    sample_index=index,
                    solution=solution,
                    score=0.0,
                    tests_passed=0,
                    tests_total=len(visible_tests),
                    error=reason,
                )
            )
            continue

        attempts.append(
            SolverAttempt(
                solver=solver,
                sample_index=index,
                solution=solution,
                score=result.score,
                tests_passed=result.tests_passed,
                tests_total=result.tests_total,
                error=result.load_error,
            )
        )

    dispatch = blocking_check(
        DISPATCH_CHECK[solver],
        passed=not infrastructure_failures,
        solver=solver,
        samples=len(solutions),
        failures=infrastructure_failures,
    )
    return attempts, dispatch


def aggregate(
    attempts: list[SolverAttempt],
    avg_check: str,
    avg_threshold: float,
    max_check: str | None = None,
    min_check: str | None = None,
) -> list[EvalResult]:
    """Reduce attempts to the measurements the predicate reads."""
    if not attempts:
        return []
    scores = [a.score for a in attempts]
    average = sum(scores) / len(scores)

    results = [
        measurement(
            avg_check,
            average,
            avg_threshold,
            samples=len(scores),
            scores=[round(s, 6) for s in scores],
        )
    ]
    if max_check is not None:
        results.append(measurement(max_check, max(scores), 1.0, samples=len(scores)))
    if min_check is not None:
        results.append(measurement(min_check, min(scores), 0.0, samples=len(scores)))
    return results
