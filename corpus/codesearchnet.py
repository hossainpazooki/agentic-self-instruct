"""Grounding-document corpus: CodeSearchNet (Python).

The analogue of the paper's S2ORC draw. Each grounding document is one
function's docstring + signature; the original body is loaded but held out and
never shown to the challenger or to any solver.

Licensing: CodeSearchNet is assembled from permissively licensed open-source
repositories and the dataset tooling is MIT. Each record carries the source
repository, and the loader records whatever licence field the shard provides,
so the licence of every grounding document travels with it rather than being
asserted once in a README.

**Provenance honesty.** If no shard is present on disk, the loader returns a
SYNTHETIC fallback and stamps ``synthetic=True`` on every record and on the
corpus descriptor. Nothing downstream may report a synthetic run as a
CodeSearchNet run. Fetch a real shard with ``scripts/fetch_codesearchnet.py``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Iterator

from repo.schemas import GroundingDocument

DEFAULT_SHARD_DIR = Path(__file__).resolve().parent.parent / "data" / "codesearchnet"


def _signature_and_body(source: str) -> tuple[str, str] | None:
    """Split a function source into its ``def`` line and its body.

    Uses the AST rather than a regex so that decorators, multi-line signatures,
    and default arguments containing colons do not silently produce a wrong
    split.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lines = source.splitlines()
        body_start = node.body[0].lineno - 1
        signature_lines = lines[node.lineno - 1 : body_start]
        signature = " ".join(line.strip() for line in signature_lines).strip()
        if not signature.endswith(":"):
            signature = signature.rstrip() + ":"
        body = "\n".join(lines[body_start:])
        return signature, body
    return None


def _iter_shard(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_documents(
    limit: int,
    shard_dir: Path | None = None,
    min_docstring_chars: int = 80,
) -> tuple[list[GroundingDocument], dict[str, Any]]:
    """Return (documents, corpus_descriptor).

    Records are yielded in shard order and filtered deterministically, so the
    same shard and the same limit always produce the same document order --
    which is what "same source-document order" across arms requires.
    """
    shard_dir = shard_dir or DEFAULT_SHARD_DIR
    shards = sorted(shard_dir.glob("*.jsonl")) if shard_dir.is_dir() else []

    if not shards:
        documents = _synthetic_documents(limit)
        return documents, {
            "name": "synthetic-fallback",
            "synthetic": True,
            "reason": f"no .jsonl shard found under {shard_dir}",
            "count": len(documents),
            "license": "n/a (generated placeholder records)",
        }

    documents: list[GroundingDocument] = []
    seen: set[str] = set()
    for shard in shards:
        for row in _iter_shard(shard):
            if len(documents) >= limit:
                break
            source = row.get("original_string") or row.get("code") or ""
            docstring = (row.get("docstring") or "").strip()
            if len(docstring) < min_docstring_chars:
                continue
            split = _signature_and_body(source)
            if split is None:
                continue
            signature, body = split
            func_name = row.get("func_name") or ""
            record_id = f"{row.get('repo', 'unknown')}::{row.get('path', '?')}::{func_name}"
            if record_id in seen:
                continue
            seen.add(record_id)
            documents.append(
                GroundingDocument(
                    record_id=record_id,
                    signature=signature,
                    docstring=docstring,
                    body=body,
                    repo_name=str(row.get("repo", "unknown")),
                    license=str(row.get("license", "unrecorded")),
                )
            )
        if len(documents) >= limit:
            break

    return documents, {
        "name": "codesearchnet-python",
        "synthetic": False,
        "shards": [s.name for s in shards],
        "count": len(documents),
        "min_docstring_chars": min_docstring_chars,
    }


def _synthetic_documents(limit: int) -> list[GroundingDocument]:
    """Placeholder grounding documents, clearly marked as such.

    These carry no real provenance. They exist so the pipeline can be exercised
    without a corpus download. With the fake backend they are behaviourally
    equivalent to real records anyway, because the fake challenger keys off the
    record id rather than the docstring -- which is exactly why a synthetic
    smoke run says nothing about prompt quality.
    """
    themes = [
        ("normalise_whitespace", "Collapse runs of whitespace in a string to single spaces."),
        ("chunk_sequence", "Split a sequence into consecutive chunks of a fixed size."),
        ("merge_intervals", "Merge a list of overlapping closed intervals."),
        ("parse_duration", "Parse a duration string such as '2h30m' into seconds."),
        ("rolling_mean", "Compute the rolling mean of a numeric series over a window."),
    ]
    documents: list[GroundingDocument] = []
    for index in range(limit):
        name, summary = themes[index % len(themes)]
        documents.append(
            GroundingDocument(
                record_id=f"synthetic::{index:04d}::{name}",
                signature=f"def {name}(value: str) -> str:",
                docstring=(
                    f"{summary}\n\n"
                    "SYNTHETIC PLACEHOLDER RECORD -- not drawn from CodeSearchNet. "
                    "Present so the pipeline can run without a corpus shard."
                ),
                body="    raise NotImplementedError\n",
                repo_name="synthetic/placeholder",
                license="n/a",
            )
        )
    return documents
