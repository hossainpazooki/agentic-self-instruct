"""Challenger subagent: grounding document -> problem + tests.

Malformed output is a blocking EvalResult with passed=False, never an
exception. A candidate that raises its way out of the loop is a missing row in
the got-away audit.
"""

from __future__ import annotations

import json
import re

from models.base import ModelClient, ModelDispatchError
from repo.prompts import load_prompt
from repo.schemas import ChallengerOutput, FailureMode, GroundingDocument
from shared import checks
from shared.evalresult import EvalResult, blocking_check

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

REQUIRED_KEYS = ("problem_statement", "signature", "reference_solution", "visible_tests")


def build_user_prompt(
    document: GroundingDocument,
    round_index: int,
    failures: list[FailureMode],
) -> str:
    """Compose the challenger turn, including the paper's three feedback slots."""
    lines = [
        f"RECORD_ID: {document.record_id}",
        f"ROUND: {round_index}",
        "",
        "GROUNDING DOCUMENT",
        f"signature: {document.signature}",
        "docstring:",
        document.docstring.strip(),
        "",
    ]

    if not failures:
        lines.append(
            "Generate a challenging programming problem with executable tests "
            "from this grounding function. Read it first."
        )
        return "\n".join(lines)

    grouped: dict[str, list[FailureMode]] = {}
    for failure in failures:
        grouped.setdefault(failure.label, []).append(failure)

    lines.append("PREVIOUS ATTEMPTS THAT DID NOT MEET CRITERIA")
    for label in ("TOO EASY", "FAILED ON STRONG", "FAILED QV"):
        entries = grouped.get(label, [])
        if not entries:
            continue
        lines.append(f"\n{label}:")
        for entry in entries:
            detail = ", ".join(f"{k}={v}" for k, v in sorted(entry.detail.items()))
            lines.append(f"  - round {entry.round_index}: {entry.problem_statement[:160]}")
            if detail:
                lines.append(f"    ({detail})")

    lines.append(
        "\nGenerate an ENTIRELY NEW problem from a DIFFERENT angle that requires "
        "deeper reasoning. Do not rephrase any attempt above."
    )
    return "\n".join(lines)


def parse_challenger_output(raw: str) -> tuple[ChallengerOutput | None, str | None]:
    """Return (output, error). Never raises on bad model output."""
    text = raw.strip()
    fenced = FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"not JSON: {exc}"

    if not isinstance(payload, dict):
        return None, f"expected a JSON object, got {type(payload).__name__}"

    missing = [k for k in REQUIRED_KEYS if k not in payload]
    if missing:
        return None, f"missing keys: {', '.join(missing)}"

    tests = payload["visible_tests"]
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        return None, "visible_tests must be a list of strings"
    if not tests:
        return None, "visible_tests is empty"

    try:
        return (
            ChallengerOutput(
                problem_statement=str(payload["problem_statement"]),
                signature=str(payload["signature"]),
                reference_solution=str(payload["reference_solution"]),
                visible_tests=[str(t) for t in tests],
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - schema violation is a report, not a crash
        return None, f"schema violation: {exc}"


def call_challenger(
    client: ModelClient,
    document: GroundingDocument,
    round_index: int,
    failures: list[FailureMode],
    temperature: float = 1.0,
) -> tuple[ChallengerOutput | None, str, EvalResult]:
    """Returns (output, exact_prompt_text, wellformedness_check).

    The exact prompt text is returned because it is what ``prompt_hash`` binds
    the candidate to. Reconstructing it later would let a mid-run prompt edit
    slip past the controller's cross-stage consistency check.
    """
    system = load_prompt("challenger").text
    user = build_user_prompt(document, round_index, failures)
    exact_prompt = f"{system}\n\n---\n\n{user}"

    try:
        raw = client.generate(system=system, user=user, n=1, temperature=temperature)[0]
    except ModelDispatchError as exc:
        return None, exact_prompt, blocking_check(
            checks.CHALLENGER_WELLFORMED, False, reason=f"dispatch failed: {exc}"
        )

    output, error = parse_challenger_output(raw)
    if output is None:
        return None, exact_prompt, blocking_check(
            checks.CHALLENGER_WELLFORMED, False, reason=error, raw_prefix=raw[:200]
        )

    return output, exact_prompt, blocking_check(checks.CHALLENGER_WELLFORMED, True)
