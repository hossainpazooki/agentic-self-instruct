"""The system-wide check contract.

Every check anywhere in this system -- in the agent's mutable harness (``repo/``)
or in the trusted controller -- emits ``EvalResult``. Nothing else crosses a
component boundary as a verdict.

Two rules make the three-arm study meaningful:

1. No check writes an acceptance bit. A check reports *what it measured*; the
   acceptance predicate is the only thing that turns measurements into a bit.
2. The predicate re-derives its own comparisons from ``score``. It does not
   trust a check's ``passed`` field for threshold questions. This is what lets a
   second predicate variant be scored as a shadow verdict over the same
   ``EvalResult`` list without re-running a single solver.

``passed`` therefore carries a narrower meaning than it looks like it carries:

* For a *blocking* check (``details["blocking"] is True``) -- the quality
  verifier, sandbox execution, challenger well-formedness -- ``passed`` is
  load-bearing. False means the candidate is dead regardless of any score.
* For a *measurement* check, ``passed`` records whether the emitting component's
  own threshold held. The predicate ignores it and applies its own.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "EvalResult",
    "measurement",
    "blocking_check",
    "index_by_name",
    "DuplicateCheckName",
]


class EvalResult(BaseModel):
    """One check's report. Frozen: a verdict is a record, not a workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    score: float
    threshold: float
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_blocking(self) -> bool:
        return bool(self.details.get("blocking", False))


def measurement(name: str, score: float, threshold: float, **details: Any) -> EvalResult:
    """A measurement check: ``passed`` reflects the emitter's own threshold."""
    return EvalResult(
        name=name,
        passed=score >= threshold,
        score=float(score),
        threshold=float(threshold),
        details={"blocking": False, **details},
    )


def blocking_check(name: str, passed: bool, **details: Any) -> EvalResult:
    """A blocking check: False kills the candidate whatever the scores say.

    Infrastructure failures (sandbox timeout, malformed challenger output,
    solver error) are emitted through here with ``passed=False``. They are
    never raised as exceptions, because an exception would drop the candidate
    out of the got-away audit entirely -- a missing row reads as "nothing
    happened" when in fact something did.
    """
    return EvalResult(
        name=name,
        passed=passed,
        score=1.0 if passed else 0.0,
        threshold=1.0,
        details={"blocking": True, **details},
    )


class DuplicateCheckName(ValueError):
    """Two checks claimed the same name. The predicate refuses to guess."""


def index_by_name(results: list[EvalResult]) -> dict[str, EvalResult]:
    """Index checks by name, refusing duplicates.

    Silently letting a later result win would make acceptance depend on list
    order, which would break predicate purity in the one way a purity test
    that reuses a single list would never notice.
    """
    out: dict[str, EvalResult] = {}
    for r in results:
        if r.name in out:
            raise DuplicateCheckName(r.name)
        out[r.name] = r
    return out
