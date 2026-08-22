"""Prompt loading with revision tracking.

Prompts are files on disk, not string literals, because the meta-optimizer
edits them as code diffs against the baseline repository -- exactly as the
paper does with its ``.opencode/prompts/`` tree. A prompt's revision is the
content hash of the file, so a candidate's provenance survives an optimizer
edit that lands mid-run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.hashing import text_hash

REPO_ROOT = Path(__file__).resolve().parent

PROMPT_PATHS = {
    "orchestrator": REPO_ROOT / "orchestrator" / "prompt.md",
    "challenger": REPO_ROOT / "challenger" / "prompt.md",
    "verifier": REPO_ROOT / "verifier" / "prompt.md",
    "weak_solver": REPO_ROOT / "solvers" / "weak_prompt.md",
    "strong_solver": REPO_ROOT / "solvers" / "strong_prompt.md",
}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    revision: str

    @property
    def short_revision(self) -> str:
        return self.revision[:12]


def load_prompt(name: str) -> Prompt:
    path = PROMPT_PATHS.get(name)
    if path is None:
        raise KeyError(f"unknown prompt: {name!r}; known: {sorted(PROMPT_PATHS)}")
    text = path.read_text(encoding="utf-8")
    return Prompt(name=name, text=text, revision=text_hash(text))


def load_all() -> dict[str, Prompt]:
    return {name: load_prompt(name) for name in PROMPT_PATHS}
