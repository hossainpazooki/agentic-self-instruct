"""Reject meta-optimizer diffs that touch anything outside ``repo/``.

This is the first of two independent defences. The second is filesystem
permissions applied by the runner (``runner/isolation.py``), because a guard
that lives in the same tree the optimizer edits is a guard the optimizer can
in principle edit. Neither is trusted alone.

The guard is deliberately paranoid about path shape, not just path prefix:
``repo/../controller/predicate.py`` has the right prefix and the wrong target.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

GIT_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)", re.MULTILINE)
OLD_FILE_RE = re.compile(r"^--- (?:a/)?(?P<path>[^\t\n]+)", re.MULTILINE)
NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(?P<path>[^\t\n]+)", re.MULTILINE)

ALLOWED_PREFIX = "repo/"
DEV_NULL = {"/dev/null", "dev/null"}


class DiffRejected(RuntimeError):
    """A diff tried to modify something outside the agent's edit surface."""


@dataclass
class DiffReport:
    paths: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and bool(self.paths)


def _normalise(raw: str) -> str:
    return posixpath.normpath(raw.strip().replace("\\", "/"))


def inspect(diff_text: str, allowed_prefix: str = ALLOWED_PREFIX) -> DiffReport:
    report = DiffReport()
    seen: set[str] = set()

    raw_paths: list[str] = []
    for match in GIT_HEADER_RE.finditer(diff_text):
        raw_paths.extend([match.group("a"), match.group("b")])
    raw_paths.extend(m.group("path") for m in OLD_FILE_RE.finditer(diff_text))
    raw_paths.extend(m.group("path") for m in NEW_FILE_RE.finditer(diff_text))

    for raw in raw_paths:
        stripped = raw.strip()
        if stripped in DEV_NULL:
            continue  # file creation/deletion marker, not a target

        if stripped.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", stripped):
            report.violations.append(f"absolute path: {stripped}")
            continue

        normalised = _normalise(stripped)
        if normalised in seen:
            continue
        seen.add(normalised)
        report.paths.append(normalised)

        # normpath has already collapsed `repo/../controller`, so a surviving
        # `..` means the path escapes the tree root outright.
        if normalised.startswith("..") or "/../" in normalised:
            report.violations.append(f"path traversal: {stripped}")
            continue

        if not normalised.startswith(allowed_prefix):
            report.violations.append(
                f"outside the edit surface ({allowed_prefix}): {normalised}"
            )

    if not report.paths:
        report.violations.append("diff touches no files")

    return report


def guard(diff_text: str, allowed_prefix: str = ALLOWED_PREFIX) -> DiffReport:
    """Raise ``DiffRejected`` unless every touched path is inside the surface.

    Loud by design. A diff that quietly no-ops when it tries to escape teaches
    the optimizer nothing and leaves no evidence in the run log.
    """
    report = inspect(diff_text, allowed_prefix)
    if not report.ok:
        raise DiffRejected(
            "meta-optimizer diff rejected: "
            + "; ".join(report.violations)
            + f" (paths: {report.paths})"
        )
    return report
