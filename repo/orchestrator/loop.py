"""The Agentic Self-Instruct inner loop.

Ported from Section 3.1 and Appendix C.1 Figure 7:

    challenger -> quality verifier -> weak solver (3 samples) -> [gate] ->
    strong solver (3 samples) -> acceptance -> final quality verifier

On failure the loop composes targeted feedback grouped as TOO EASY / FAILED ON
STRONG / FAILED QV and asks the challenger for a new problem from a different
angle. It stops on acceptance or on step-budget exhaustion.

Two structural decisions worth stating plainly:

1. **The acceptance predicate is injected.** The loop never imports
   ``repo.acceptance``. In arms 1 and 2 the runner injects the in-repo
   predicate; in arm 3 it injects the controller's. This is what lets arm 3
   run against a workspace with ``repo/acceptance/`` physically deleted.

2. **The controller probe fires the moment a well-formed candidate exists**,
   before any solver is dispatched. If it fired after the weak gate instead,
   the short-circuit would silently exclude most candidates from the got-away
   audit -- and a candidate missing from the audit reads as one that never
   existed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from pydantic import BaseModel, ConfigDict, Field

from models.base import ModelClient
from repo.challenger.client import call_challenger
from repo.judge.runner import aggregate, score_solutions
from repo.judge.sandbox import Sandbox
from repo.orchestrator.feedback import classify
from repo.schemas import Candidate, FailureMode, GroundingDocument, SolverAttempt
from repo.solvers.clients import call_solver
from repo.verifier.client import call_verifier
from shared import checks
from shared.evalresult import EvalResult, blocking_check, measurement

AcceptanceFn = Callable[[list[EvalResult]], bool]


class ControllerProbe(Protocol):
    """The controller's read-only view. Runs in every arm."""

    def __call__(self, candidate: Candidate) -> list[EvalResult]: ...


@dataclass
class WeakGate:
    """Compute-saving gate from Figure 7's workflow, not the acceptance predicate.

    Section 3.1: "we save compute at each iteration by having the judge evaluate
    the strong solver only if the weak solver passes its corresponding success
    criterion." In the paper this lives in the main-agent prompt's workflow
    (``--weak-only`` then ``--strong-only``), not in the acceptance script --
    which is why it lives here and survives arm 3's removal of
    ``repo/acceptance/``.
    """

    max_avg: float = 0.65
    max_best: float = 0.75
    require_nonzero: bool = True
    enabled: bool = True

    def passes(self, attempts: list[SolverAttempt]) -> bool:
        if not self.enabled:
            return True
        if not attempts:
            return False
        scores = [a.score for a in attempts]
        if sum(scores) / len(scores) > self.max_avg:
            return False
        if max(scores) > self.max_best:
            return False
        if self.require_nonzero and min(scores) <= 0.0:
            return False
        return True


@dataclass
class LoopConfig:
    step_budget: int = 6
    samples_per_solver: int = 3
    weak_gate: WeakGate = field(default_factory=WeakGate)
    run_final_verifier: bool = True


class RoundRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    round_index: int
    candidate: Candidate | None
    harness_results: list[EvalResult] = Field(default_factory=list)
    controller_results: list[EvalResult] = Field(default_factory=list)
    accepted: bool = False
    decided_by: str = "repo"
    failure: FailureMode | None = None
    weak_attempts: list[SolverAttempt] = Field(default_factory=list)
    strong_attempts: list[SolverAttempt] = Field(default_factory=list)


class DocumentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    rounds: list[RoundRecord] = Field(default_factory=list)
    accepted: bool = False
    accepted_round: int | None = None
    exhausted_budget: bool = False


class Orchestrator:
    def __init__(
        self,
        challenger: ModelClient,
        verifier: ModelClient,
        weak_solver: ModelClient,
        strong_solver: ModelClient,
        sandbox: Sandbox,
        accept: AcceptanceFn,
        controller_probe: ControllerProbe,
        config: LoopConfig | None = None,
        decided_by: str = "repo",
    ) -> None:
        self.challenger = challenger
        self.verifier = verifier
        self.weak_solver = weak_solver
        self.strong_solver = strong_solver
        self.sandbox = sandbox
        self.accept = accept
        self.controller_probe = controller_probe
        self.config = config or LoopConfig()
        self.decided_by = decided_by

    def run_document(self, document: GroundingDocument) -> DocumentResult:
        result = DocumentResult(record_id=document.record_id)
        failures: list[FailureMode] = []

        for round_index in range(self.config.step_budget):
            record = self._run_round(document, round_index, failures)
            result.rounds.append(record)

            if record.accepted:
                result.accepted = True
                result.accepted_round = round_index
                return result

            if record.failure is not None:
                failures.append(record.failure)

        result.exhausted_budget = True
        return result

    def _run_round(
        self, document: GroundingDocument, round_index: int, failures: list[FailureMode]
    ) -> RoundRecord:
        from repo.prompts import load_prompt

        record = RoundRecord(
            record_id=document.record_id,
            round_index=round_index,
            candidate=None,
            decided_by=self.decided_by,
        )

        # (1) challenger
        output, exact_prompt, wellformed = call_challenger(
            self.challenger, document, round_index, failures
        )
        record.harness_results.append(wellformed)
        if output is None:
            # Malformed output is a recorded rejection, not a dropped candidate.
            record.failure = FailureMode(
                label="FAILED QV",
                round_index=round_index,
                problem_statement="<malformed challenger output>",
                detail={"reason": wellformed.details.get("reason")},
            )
            return record

        candidate = Candidate(
            candidate_id=f"{document.record_id}:r{round_index}",
            record_id=document.record_id,
            round_index=round_index,
            output=output,
            challenger_prompt=exact_prompt,
            verifier_prompt_revision=load_prompt("verifier").short_revision,
        )
        record.candidate = candidate

        # Controller probe: every candidate, every arm, before any gating.
        record.controller_results = list(self.controller_probe(candidate))

        # (2) quality verifier, pre-dispatch
        verifier_result = call_verifier(self.verifier, document, output, "pre_dispatch")
        record.harness_results.append(verifier_result)
        if not verifier_result.passed:
            record.failure = classify(round_index, output, record.harness_results)
            record.accepted = self._decide(record)
            return record

        # (4) weak solver only
        weak_dispatch = call_solver(
            self.weak_solver, "weak_solver", document.record_id, output,
            samples=self.config.samples_per_solver,
        )
        weak_attempts, weak_check = score_solutions(
            self.sandbox, "weak", weak_dispatch.solutions, output.visible_tests
        )
        if weak_dispatch.error is not None:
            weak_check = blocking_check(
                checks.WEAK_DISPATCH, False, reason=weak_dispatch.error
            )
        record.weak_attempts = weak_attempts
        record.harness_results.append(weak_check)
        record.harness_results.extend(
            aggregate(weak_attempts, checks.WEAK_AVG, 0.65, checks.WEAK_MAX, checks.WEAK_MIN)
        )

        # (5) weak gate -- the compute short-circuit
        if not self.config.weak_gate.passes(weak_attempts):
            record.failure = classify(round_index, output, record.harness_results)
            record.accepted = self._decide(record)
            return record

        # (6) strong solver
        strong_dispatch = call_solver(
            self.strong_solver, "strong_solver", document.record_id, output,
            samples=self.config.samples_per_solver,
        )
        strong_attempts, strong_check = score_solutions(
            self.sandbox, "strong", strong_dispatch.solutions, output.visible_tests
        )
        if strong_dispatch.error is not None:
            strong_check = blocking_check(
                checks.STRONG_DISPATCH, False, reason=strong_dispatch.error
            )
        record.strong_attempts = strong_attempts
        record.harness_results.append(strong_check)
        record.harness_results.extend(aggregate(strong_attempts, checks.STRONG_AVG, 0.60))

        weak_avg = _mean(weak_attempts)
        strong_avg = _mean(strong_attempts)
        record.harness_results.append(
            measurement(
                checks.SOLVER_GAP, strong_avg - weak_avg, 0.20,
                weak_avg=round(weak_avg, 6), strong_avg=round(strong_avg, 6),
            )
        )

        # (7)/(8) acceptance, then the final verifier pass
        provisional = self._decide(record)
        if provisional and self.config.run_final_verifier:
            final = call_verifier(self.verifier, document, output, "final")
            final = final.model_copy(update={"name": checks.QUALITY_VERIFIER_FINAL})
            record.harness_results.append(final)
            provisional = self._decide(record)

        record.accepted = provisional
        if not record.accepted:
            record.failure = classify(round_index, output, record.harness_results)
        return record

    def _decide(self, record: RoundRecord) -> bool:
        """Arms 1-2 decide on harness results; arm 3 decides on controller results."""
        if self.decided_by == "controller":
            return self.accept(record.controller_results)
        return self.accept(record.harness_results)


def _mean(attempts: list[SolverAttempt]) -> float:
    if not attempts:
        return 0.0
    return sum(a.score for a in attempts) / len(attempts)
