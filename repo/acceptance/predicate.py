"""The acceptance predicate: list[EvalResult] -> bool. Pure.

PRESENT in arms 1 and 2 (inside the agent's mutable harness).
ABSENT in arm 3, where controller/predicate.py decides instead.

Purity means: no file access, no network, no clock, no randomness, no logging,
no mutation of the input. The same list yields the same bit forever. This is
what makes an arm-1 verdict and an arm-3 verdict comparable at all, and it is
the property the meta-optimizer would otherwise be free to erode.

Two variants, because the paper specifies the CS predicate two ways:

``deployed_c1`` (default) -- Appendix C.1 Figure 7, the system prompt that
    actually ran, corroborated by the Section 4 setup paragraph and by the
    verbatim prompt in the RAM README:
        weak_avg <= 0.65, max_weak <= 0.75, no zero-scoring weak attempt,
        strong_avg >= 0.60 and strong_avg < 0.95, gap >= 0.20

``prose_s31`` -- the Section 3.1 body text:
        weak_avg < 0.50, strong_avg >= 0.65, gap >= 0.20

They are not a numeric tweak of each other. The deployed form adds a
best-of-attempts cap, a no-zeros rule, and an upper bound on the strong solver
(reject problems the strong solver finds trivial) that the prose form lacks.
See docs/fidelity.md.
"""

from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared import checks
from shared.evalresult import EvalResult, index_by_name

PredicateVariant = Literal["deployed_c1", "prose_s31"]

__all__ = [
    "Verdict",
    "accept",
    "explain",
    "VARIANTS",
    "required_measurements",
]


class Condition(BaseModel):
    """One threshold comparison the predicate applies to a measured score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: str
    op: Literal["<", "<=", ">", ">=", "=="]
    bound: float

    def holds(self, score: float) -> bool:
        if self.op == "<":
            return score < self.bound
        if self.op == "<=":
            return score <= self.bound
        if self.op == ">":
            return score > self.bound
        if self.op == ">=":
            return score >= self.bound
        return score == self.bound

    def describe(self, score: float) -> str:
        return f"{self.check}={score:.4f} {self.op} {self.bound}"


# Declarative, so that "what does this arm accept" is readable rather than traced.
VARIANTS: dict[PredicateVariant, tuple[Condition, ...]] = {
    "deployed_c1": (
        Condition(check=checks.WEAK_AVG, op="<=", bound=0.65),
        Condition(check=checks.WEAK_MAX, op="<=", bound=0.75),
        # "no zeros" -- read as PER ATTEMPT, not "not all attempts". The
        # deployed prompt's strong-side twin spells the same rule out as "No
        # individual strong = 0%?", which settles the ambiguity in the weak
        # block's terser phrasing. Section 6's separate remark about "avoiding
        # all-zero weak rollouts" describes an emergent behaviour of the legal
        # loop, not this criterion. The looser reading is a real alternative:
        # see docs/fidelity.md.
        Condition(check=checks.WEAK_MIN, op=">", bound=0.0),
        Condition(check=checks.STRONG_AVG, op=">=", bound=0.60),
        Condition(check=checks.STRONG_AVG, op="<", bound=0.95),
        # "No individual strong = 0%? (suspicious)" -- the strong-side twin of
        # the weak "no zeros" rule. A strong rollout at zero usually means the
        # problem is malformed rather than hard.
        Condition(check=checks.STRONG_MIN, op=">", bound=0.0),
        Condition(check=checks.SOLVER_GAP, op=">=", bound=0.20),
    ),
    "prose_s31": (
        Condition(check=checks.WEAK_AVG, op="<", bound=0.50),
        Condition(check=checks.STRONG_AVG, op=">=", bound=0.65),
        Condition(check=checks.SOLVER_GAP, op=">=", bound=0.20),
    ),
}


def required_measurements(variant: PredicateVariant) -> frozenset[str]:
    return frozenset(c.check for c in VARIANTS[variant])


class Verdict(BaseModel):
    """Why the bit came out the way it did. Derived purely from the input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variant: PredicateVariant
    accepted: bool
    failed_blocking: list[str] = Field(default_factory=list)
    missing_measurements: list[str] = Field(default_factory=list)
    failed_conditions: list[str] = Field(default_factory=list)
    satisfied_conditions: list[str] = Field(default_factory=list)


def explain(results: list[EvalResult], variant: PredicateVariant = "deployed_c1") -> Verdict:
    """Pure. Raises only on a malformed check list (duplicate names)."""
    by_name = index_by_name(results)

    # 1. Blocking checks. A failed quality verifier, a sandbox timeout, or
    #    malformed challenger output kills the candidate whatever the scores
    #    say. These arrive as EvalResults with passed=False, never exceptions,
    #    so the candidate still occupies a row in the got-away audit.
    failed_blocking = sorted(r.name for r in by_name.values() if r.is_blocking and not r.passed)

    # 2. Required measurements. Absence is a rejection, not an error. Under the
    #    weak-first short-circuit the strong solver is never dispatched when the
    #    weak criterion fails, so strong_avg and solver_gap are legitimately
    #    absent on most rejected candidates.
    required = required_measurements(variant)
    missing = sorted(name for name in required if name not in by_name)

    failed: list[str] = []
    satisfied: list[str] = []
    for condition in VARIANTS[variant]:
        result = by_name.get(condition.check)
        if result is None:
            continue
        # The predicate re-derives from `score` and ignores the emitting
        # check's own `passed`. That is what lets the other variant be scored
        # as a shadow verdict over this same list without re-running solvers.
        (satisfied if condition.holds(result.score) else failed).append(
            condition.describe(result.score)
        )

    accepted = not failed_blocking and not missing and not failed
    return Verdict(
        variant=variant,
        accepted=accepted,
        failed_blocking=failed_blocking,
        missing_measurements=missing,
        failed_conditions=failed,
        satisfied_conditions=satisfied,
    )


def accept(results: list[EvalResult], variant: PredicateVariant = "deployed_c1") -> bool:
    """The contract: list[EvalResult] -> bool."""
    return explain(results, variant).accepted


def predicate_for(variant: PredicateVariant) -> Callable[[list[EvalResult]], bool]:
    def _predicate(results: list[EvalResult]) -> bool:
        return accept(results, variant)

    return _predicate
