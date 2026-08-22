"""Weak and strong solver dispatch.

Both solvers are invoked 3 times, per Section 3.1 ("each invoked 3 times to
reduce variance"). Sampling temperature is 1.0, per Section 4's note that
single-evaluation scores are noisy because solvers run at temperature 1.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from models.base import ModelClient, ModelDispatchError
from repo.prompts import load_prompt
from repo.schemas import ChallengerOutput

FENCE_RE = re.compile(r"```(?:python|py)?\s*(.*?)```", re.DOTALL)

SAMPLES_PER_SOLVER = 3
SOLVER_TEMPERATURE = 1.0


def strip_fences(text: str) -> str:
    match = FENCE_RE.search(text)
    return (match.group(1) if match else text).strip()


def build_user_prompt(record_id: str, output: ChallengerOutput) -> str:
    return (
        f"RECORD_ID: {record_id}\n\n"
        f"PROBLEM\n{output.problem_statement}\n\n"
        f"SIGNATURE\n{output.signature}\n\n"
        "Implement the function. Return only Python source."
    )


@dataclass
class SolverDispatch:
    solutions: list[str]
    error: str | None


def call_solver(
    client: ModelClient,
    prompt_name: str,
    record_id: str,
    output: ChallengerOutput,
    samples: int = SAMPLES_PER_SOLVER,
) -> SolverDispatch:
    """Never raises. A dispatch failure comes back as ``error`` and is turned
    into a blocking EvalResult by the caller."""
    system = load_prompt(prompt_name).text
    user = build_user_prompt(record_id, output)

    try:
        raw = client.generate(
            system=system, user=user, n=samples, temperature=SOLVER_TEMPERATURE
        )
    except ModelDispatchError as exc:
        return SolverDispatch(solutions=[""] * samples, error=str(exc))

    if len(raw) != samples:
        return SolverDispatch(
            solutions=([*raw] + [""] * samples)[:samples],
            error=f"expected {samples} samples, got {len(raw)}",
        )

    return SolverDispatch(solutions=[strip_fences(r) for r in raw], error=None)
