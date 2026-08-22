"""The EvalResult contract."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.evalresult import (
    DuplicateCheckName,
    EvalResult,
    blocking_check,
    index_by_name,
    measurement,
)


def test_required_fields():
    result = EvalResult(name="x", passed=True, score=0.5, threshold=0.4, details={})
    assert (result.name, result.passed, result.score, result.threshold) == ("x", True, 0.5, 0.4)


def test_is_frozen():
    result = measurement("x", 0.5, 0.4)
    with pytest.raises(ValidationError):
        result.name = "y"


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        EvalResult(name="x", passed=True, score=1.0, threshold=1.0, verdict="accept")


def test_measurement_is_not_blocking_but_blocking_check_is():
    assert measurement("x", 0.9, 0.5).is_blocking is False
    assert blocking_check("y", True).is_blocking is True


def test_blocking_check_failure_carries_a_reason_not_an_exception():
    result = blocking_check("sandbox", False, reason="timeout")
    assert result.passed is False and result.details["reason"] == "timeout"


def test_index_by_name_refuses_duplicates():
    # Letting a later result win would make acceptance depend on list order.
    with pytest.raises(DuplicateCheckName):
        index_by_name([measurement("x", 0.1, 0.0), measurement("x", 0.9, 0.0)])
