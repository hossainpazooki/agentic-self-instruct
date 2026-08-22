"""Typed payloads crossing subagent boundaries inside the harness.

Departure from the paper (recorded in docs/fidelity.md): the challenger emits
problem_statement / signature / reference_solution / visible_tests where the
paper's CS challenger emits context / question / reference_answer / rubric.
The slot structure is preserved one-for-one:

    context           -> problem_statement
    question          -> signature      (what the solver must produce)
    reference_answer  -> reference_solution
    rubric            -> visible_tests  (the scoreable criteria)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.hashing import prompt_hash as _prompt_hash
from shared.hashing import rubric_provenance as _rubric_provenance


class GroundingDocument(BaseModel):
    """The analogue of one CS paper: a single function docstring + signature.

    ``body`` is the original implementation. It is held out -- never shown to
    the challenger or to any solver -- and exists so the corpus loader can be
    audited and a human can read what the docstring was describing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    signature: str
    docstring: str
    body: str = Field(repr=False)
    repo_name: str
    license: str
    language: str = "python"


class ChallengerOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    problem_statement: str
    signature: str
    reference_solution: str
    visible_tests: list[str]


class Candidate(BaseModel):
    """A generated training example plus the provenance the controller checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    record_id: str
    round_index: int
    output: ChallengerOutput
    challenger_prompt: str = Field(repr=False)
    verifier_prompt_revision: str

    @property
    def prompt_hash(self) -> str:
        return _prompt_hash(self.challenger_prompt)

    @property
    def rubric_provenance(self) -> str:
        return _rubric_provenance(self.output.visible_tests, self.verifier_prompt_revision)


class SolverAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    solver: Literal["weak", "strong", "reference"]
    sample_index: int
    solution: str
    score: float
    tests_passed: int
    tests_total: int
    error: str | None = None


class FailureMode(BaseModel):
    """Feedback slot for the next challenger round.

    The paper groups prior failures as TOO EASY (with weak scores), FAILED ON
    STRONG (with gap information), and FAILED QV. Those labels are kept verbatim
    so the refinement prompt matches Appendix C.1 Figure 7.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: Literal["TOO EASY", "FAILED ON STRONG", "FAILED QV"]
    round_index: int
    problem_statement: str
    detail: dict[str, Any] = Field(default_factory=dict)
