"""Content hashing for provenance keys.

Text is normalised before hashing so a CRLF checkout on Windows and an LF
checkout on Linux produce the same ``prompt_hash``. Hash what the pipeline
consumes, not the raw bytes on disk -- a gate stricter than its own reader
emits only false alarms, and false alarms get silenced.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = ["canonical_bytes", "text_hash", "json_hash", "prompt_hash", "rubric_provenance"]


def canonical_bytes(text: str) -> bytes:
    """Normalise line endings and trailing whitespace, then UTF-8 encode."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip().encode("utf-8")


def text_hash(text: str) -> str:
    return hashlib.sha256(canonical_bytes(text)).hexdigest()


def json_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def prompt_hash(challenger_prompt: str) -> str:
    """Hash of the exact challenger prompt text that produced a candidate."""
    return text_hash(challenger_prompt)


def rubric_provenance(visible_tests: list[str], verifier_prompt_revision: str) -> str:
    """Hash binding the accepted tests to the verifier revision that cleared them.

    The paper's rubric is this system's ``visible_tests``; its quality-verifier
    prompt is editable by the meta-optimizer, so the revision that passed a
    candidate is part of that candidate's provenance, not ambient state.
    """
    return json_hash(
        {"visible_tests": list(visible_tests), "verifier_revision": verifier_prompt_revision}
    )
