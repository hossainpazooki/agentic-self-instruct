"""Meta-optimization of the data scientist agent (Section 4).

Ported step for step:

  1. Boltzmann parent selection, P(c) proportional to exp(score_c / T), T = 0.1
  2. evaluate the parent on a minibatch of training documents
  3. an analyzer LLM reads the trajectories and writes a root-cause analysis
  4. an implementer LLM writes a diff against the baseline repository
  5. re-evaluate parent and mutant on held-out validation documents
  6. accept the mutant only if its validation score STRICTLY exceeds the parent's
  7. append the outcome to a history log the next analyzer reads

Plus Section 4's noise handling: accepted candidates accumulate additional
evaluations whenever they are re-sampled as parents, and the reported score is
the average over all re-evaluations.

Departure, recorded in docs/fidelity.md: the paper runs iterations
concurrently. This runs them sequentially. Concurrency changes wall-clock and
the interleaving of history-log writes, not the search semantics -- and a
sequential loop is reproducible from a seed, which the three-arm comparison
needs more than it needs speed.

**Fitness.** The paper's Section 4 success criterion is not its Section 3.1
acceptance predicate; it is the wider five-part form. Here fitness is the
active arm's acceptance predicate, so that what the optimizer is pushed toward
is exactly what the arm accepts. That is the point of the study, and it is a
departure from the paper, which used a fixed criterion in both places.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field

from repo.meta_opt.diff_guard import DiffRejected, guard
from repo.schemas import GroundingDocument

TEMPERATURE = 0.1


class Candidate(BaseModel):
    """A population member: a diff against the baseline repository."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    parent_id: str | None
    diff: str = Field(repr=False)
    scores: list[float] = Field(default_factory=list)

    @property
    def score(self) -> float:
        """Average across all re-evaluations, per Section 4's noise handling."""
        return sum(self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def evaluations(self) -> int:
        return len(self.scores)


class IterationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    parent_id: str
    mutant_id: str | None
    parent_validation: float | None = None
    mutant_validation: float | None = None
    accepted: bool = False
    rejected_reason: str | None = None
    analysis: str = ""


# A run of the harness over a document set, returning the fraction of documents
# for which a candidate was accepted. Injected so the optimizer never needs to
# know which arm it is serving.
EvaluateFn = Callable[[str, list[GroundingDocument]], tuple[float, list[str]]]


@dataclass
class MetaOptimizerConfig:
    iterations: int = 8
    minibatch_size: int = 4
    validation_size: int = 4
    seed: int = 0
    temperature: float = TEMPERATURE
    allowed_prefix: str = "repo/"


@dataclass
class HistoryLog:
    """Append-only outcome log that subsequent analyzers read (step 7)."""

    entries: list[IterationOutcome] = field(default_factory=list)

    def append(self, outcome: IterationOutcome) -> None:
        self.entries.append(outcome)

    def render(self, limit: int = 10) -> str:
        if not self.entries:
            return "(no prior iterations)"
        lines = []
        for entry in self.entries[-limit:]:
            verdict = "ACCEPTED" if entry.accepted else "REJECTED"
            reason = f" ({entry.rejected_reason})" if entry.rejected_reason else ""
            lines.append(
                f"iter {entry.iteration}: parent={entry.parent_id} "
                f"parent_val={entry.parent_validation} mutant_val={entry.mutant_validation} "
                f"-> {verdict}{reason}"
            )
        return "\n".join(lines)


def boltzmann_select(
    population: list[Candidate], rng: random.Random, temperature: float = TEMPERATURE
) -> Candidate:
    """P(c) proportional to exp(score_c / T), T = 0.1.

    Scores are shifted by the maximum before exponentiating. With T = 0.1 a
    score of 0.8 exponentiates to e^8, and a handful of candidates overflows
    the float range outright -- the shift is arithmetically identical and does
    not.
    """
    if not population:
        raise ValueError("empty population")
    if len(population) == 1:
        return population[0]

    top = max(c.score for c in population)
    weights = [math.exp((c.score - top) / temperature) for c in population]
    return rng.choices(population, weights=weights, k=1)[0]


class MetaOptimizer:
    def __init__(
        self,
        analyzer,
        implementer,
        evaluate: EvaluateFn,
        train_documents: list[GroundingDocument],
        validation_documents: list[GroundingDocument],
        config: MetaOptimizerConfig | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.implementer = implementer
        self.evaluate = evaluate
        self.train_documents = train_documents
        self.validation_documents = validation_documents
        self.config = config or MetaOptimizerConfig()
        self.history = HistoryLog()
        self.rng = random.Random(self.config.seed)
        self.population: list[Candidate] = [
            Candidate(candidate_id="baseline", parent_id=None, diff="")
        ]

    def run(self) -> list[IterationOutcome]:
        outcomes: list[IterationOutcome] = []
        for iteration in range(self.config.iterations):
            outcomes.append(self._iterate(iteration))
        return outcomes

    def _iterate(self, iteration: int) -> IterationOutcome:
        rng = self.rng

        # (1) select
        parent = boltzmann_select(self.population, rng, self.config.temperature)

        # (2) evaluate the parent on a training minibatch, collecting trajectories
        minibatch = self._sample(self.train_documents, self.config.minibatch_size)
        _, trajectories = self.evaluate(parent.diff, minibatch)

        # (3) analyze
        analysis = self._analyze(trajectories)

        # (4) implement
        diff = self._implement(analysis, parent)
        try:
            guard(diff, self.config.allowed_prefix)
        except DiffRejected as exc:
            # Loud. The iteration is recorded as rejected with the reason, so a
            # run that spends itself attacking the controller is visible in the
            # history log rather than looking like a run of unlucky mutations.
            outcome = IterationOutcome(
                iteration=iteration,
                parent_id=parent.candidate_id,
                mutant_id=None,
                accepted=False,
                rejected_reason=f"diff guard: {exc}",
                analysis=analysis,
            )
            self.history.append(outcome)
            raise

        # (5) re-evaluate parent and mutant on held-out validation documents
        validation = self._sample(self.validation_documents, self.config.validation_size)
        parent_score, _ = self.evaluate(parent.diff, validation)
        mutant_diff = _compose(parent.diff, diff)
        mutant_score, _ = self.evaluate(mutant_diff, validation)

        # Section 4: a re-sampled parent accumulates evaluations, and the
        # reported score is the average over all of them.
        parent.scores.append(parent_score)

        mutant_id = f"iter{iteration}"
        accepted = mutant_score > parent_score  # (6) strictly exceeds
        if accepted:
            self.population.append(
                Candidate(
                    candidate_id=mutant_id,
                    parent_id=parent.candidate_id,
                    diff=mutant_diff,
                    scores=[mutant_score],
                )
            )

        outcome = IterationOutcome(
            iteration=iteration,
            parent_id=parent.candidate_id,
            mutant_id=mutant_id,
            parent_validation=parent_score,
            mutant_validation=mutant_score,
            accepted=accepted,
            rejected_reason=None if accepted else "validation score did not strictly exceed parent",
            analysis=analysis,
        )
        self.history.append(outcome)  # (7)
        return outcome

    def _sample(self, documents: list[GroundingDocument], size: int) -> list[GroundingDocument]:
        if size >= len(documents):
            return list(documents)
        return self.rng.sample(documents, size)

    def _analyze(self, trajectories: list[str]) -> str:
        user = (
            "ITERATION HISTORY\n"
            f"{self.history.render()}\n\n"
            "TRAJECTORIES\n" + "\n".join(trajectories[:20])
        )
        return self.analyzer.generate(
            system="You analyze data-generation trajectories and write a root-cause "
            "analysis of systematic failure patterns.",
            user=user,
            n=1,
            temperature=0.0,
        )[0]

    def _implement(self, analysis: str, parent: Candidate) -> str:
        user = (
            f"ROOT CAUSE ANALYSIS\n{analysis}\n\n"
            f"ITERATION HISTORY\n{self.history.render()}\n\n"
            f"CURRENT DIFF\n{parent.diff or '(baseline)'}\n\n"
            "Produce a unified diff improving the harness prompts. You may only "
            "modify files under repo/."
        )
        return self.implementer.generate(
            system="You edit the agent harness by emitting unified diffs.",
            user=user,
            n=1,
            temperature=0.0,
        )[0]


def _compose(parent_diff: str, new_diff: str) -> str:
    """Stack a mutation on its parent.

    Kept as concatenation deliberately: the population member IS the cumulative
    diff against the baseline, as in the paper, and collapsing overlapping
    hunks would silently discard the parent's edits.
    """
    return f"{parent_diff}\n{new_diff}".strip() if parent_diff else new_diff
