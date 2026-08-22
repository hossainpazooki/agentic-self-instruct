"""Quality verifier subagent.

Runs twice per the paper: before solver dispatch, and again as a final pass at
the end of the loop. Stays an LLM check -- the departure to executable judging
covers the in-loop *judge*, not the verifier.

Its prompt file is editable by the meta-optimizer, which is faithful to the
paper and is the property arm 3 exists to test.
"""

from __future__ import annotations

import re

from models.base import ModelClient, ModelDispatchError
from repo.prompts import load_prompt
from repo.schemas import ChallengerOutput, GroundingDocument
from shared import checks
from shared.evalresult import EvalResult, blocking_check

OVERALL_RE = re.compile(r"OVERALL:\s*(PASS|FAIL)", re.IGNORECASE)
FEEDBACK_RE = re.compile(r"FEEDBACK:\s*(.+)", re.IGNORECASE)


def build_user_prompt(
    document: GroundingDocument, output: ChallengerOutput, pass_label: str
) -> str:
    tests = "\n".join(f"  {t}" for t in output.visible_tests)
    return (
        f"RECORD_ID: {document.record_id}\n"
        f"PASS: {pass_label}\n\n"
        "GROUNDING DOCUMENT\n"
        f"signature: {document.signature}\n"
        f"docstring:\n{document.docstring.strip()}\n\n"
        "CANDIDATE\n"
        f"problem_statement:\n{output.problem_statement}\n\n"
        f"signature: {output.signature}\n\n"
        f"reference_solution:\n{output.reference_solution}\n\n"
        f"visible_tests ({len(output.visible_tests)}):\n{tests}\n"
    )


def call_verifier(
    client: ModelClient,
    document: GroundingDocument,
    output: ChallengerOutput,
    pass_label: str = "pre_dispatch",
) -> EvalResult:
    system = load_prompt("verifier").text
    user = build_user_prompt(document, output, pass_label)

    try:
        raw = client.generate(system=system, user=user, n=1, temperature=0.0)[0]
    except ModelDispatchError as exc:
        return blocking_check(
            checks.QUALITY_VERIFIER, False, pass_label=pass_label, reason=f"dispatch failed: {exc}"
        )

    match = OVERALL_RE.search(raw)
    if match is None:
        # An unparseable verdict is a failure, not a pass. A verifier whose
        # output cannot be read has not cleared anything.
        return blocking_check(
            checks.QUALITY_VERIFIER,
            False,
            pass_label=pass_label,
            reason="no OVERALL verdict in verifier output",
            raw_prefix=raw[:200],
        )

    passed = match.group(1).upper() == "PASS"
    feedback = FEEDBACK_RE.search(raw)
    return blocking_check(
        checks.QUALITY_VERIFIER,
        passed,
        pass_label=pass_label,
        feedback=(feedback.group(1).strip() if feedback else None),
    )
