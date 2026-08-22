"""Filesystem enforcement of the arm boundary.

The study spec requires that the controller repository is kept off the
meta-optimizer's path "with filesystem permissions in the runner, not by
convention". Three layers, because each has a hole the others cover:

1. **Structural** -- assert the controller root is not inside the harness tree.
   A permission bit is no help if the controller is a subdirectory of the thing
   being edited.
2. **Permissions** -- drop write access on the controller tree for the account
   the agent runs as (``icacls`` deny-write on Windows, ``chmod a-w`` on POSIX).
3. **Import hygiene** -- assert the harness workspace contains no import of the
   controller package, so the controller cannot be reached in-process even
   where the filesystem allows it.

Honest limit, stated once here and again in docs/fidelity.md: layer 2 is not a
real boundary when the agent runs as the same OS user as the runner, because
that user can restore the permission it just dropped. A genuine boundary needs
a separate user or a container, which is why ``ARM_CONTAINER_NOTE`` exists and
why the smoke run records which enforcement level was actually achieved rather
than claiming the strongest one.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ARM_CONTAINER_NOTE = (
    "Permission-level isolation only. For a run with real models, execute the "
    "harness in a container that never mounts the controller repository."
)


class IsolationViolation(RuntimeError):
    """The arm boundary is not intact. Never downgraded to a warning."""


@dataclass
class IsolationReport:
    controller_root: Path
    harness_root: Path
    structural_ok: bool = False
    permissions_applied: bool = False
    imports_clean: bool = False
    level: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.structural_ok and self.imports_clean


def assert_outside(controller_root: Path, harness_root: Path) -> None:
    controller_root = controller_root.resolve()
    harness_root = harness_root.resolve()
    if controller_root == harness_root or controller_root.is_relative_to(harness_root):
        raise IsolationViolation(
            f"controller root {controller_root} is inside the harness tree {harness_root}; "
            "the controller must live in a separate repository"
        )


def assert_acceptance_absent(workspace: Path) -> None:
    """Arm 3 removes ``repo/acceptance/`` outright rather than bypassing it."""
    acceptance = workspace / "repo" / "acceptance"
    if acceptance.exists():
        raise IsolationViolation(
            f"arm 3 workspace still contains {acceptance}; the in-repo predicate "
            "must be removed, not merely unused"
        )


def assert_no_controller_imports(workspace: Path) -> list[str]:
    """Reject any harness source that imports the controller package."""
    offenders: list[str] = []
    for path in workspace.rglob("*.py"):
        if any(part in {".git", "__pycache__", "tests", "runner"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import controller", "from controller")):
                offenders.append(f"{path}: {stripped}")
    return offenders


def drop_write_access(target: Path) -> tuple[bool, str]:
    """Best-effort removal of write access. Reports what actually happened."""
    target = target.resolve()
    if not target.exists():
        return False, f"{target} does not exist"

    if sys.platform == "win32":
        account = os.environ.get("USERNAME")
        if not account:
            return False, "USERNAME not set; cannot scope an icacls deny rule"
        try:
            completed = subprocess.run(
                ["icacls", str(target), "/deny", f"{account}:(OI)(CI)(W)", "/T", "/C"],
                capture_output=True, text=True, timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return False, f"icacls unavailable: {exc}"
        if completed.returncode != 0:
            return False, f"icacls failed: {completed.stderr.strip()[:200]}"
        return True, f"icacls deny-write applied for {account}"

    try:
        for path in [target, *target.rglob("*")]:
            mode = path.stat().st_mode
            path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    except OSError as exc:
        return False, f"chmod failed: {exc}"
    return True, "chmod a-w applied"


def restore_write_access(target: Path) -> None:
    """Undo ``drop_write_access`` so a test run does not leave a repo read-only."""
    target = target.resolve()
    if not target.exists():
        return
    if sys.platform == "win32":
        account = os.environ.get("USERNAME")
        if account:
            subprocess.run(
                ["icacls", str(target), "/remove:d", account, "/T", "/C"],
                capture_output=True, text=True, timeout=120,
            )
        return
    for path in [target, *target.rglob("*")]:
        try:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        except OSError:
            pass


def enforce(
    controller_root: Path,
    harness_root: Path,
    apply_permissions: bool = False,
) -> IsolationReport:
    report = IsolationReport(controller_root=controller_root, harness_root=harness_root)

    assert_outside(controller_root, harness_root)
    report.structural_ok = True

    offenders = assert_no_controller_imports(harness_root)
    report.imports_clean = not offenders
    if offenders:
        raise IsolationViolation(
            "harness imports the controller package: " + "; ".join(offenders[:5])
        )

    if apply_permissions:
        applied, note = drop_write_access(controller_root)
        report.permissions_applied = applied
        report.notes.append(note)

    report.level = "permissions" if report.permissions_applied else "structural"
    report.notes.append(ARM_CONTAINER_NOTE)
    return report


def materialize_arm_workspace(source_repo: Path, destination: Path, arm: int) -> Path:
    """Copy ``repo/`` into a per-arm workspace, deleting ``acceptance/`` for arm 3."""
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    shutil.copytree(source_repo / "repo", destination / "repo")

    if arm == 3:
        shutil.rmtree(destination / "repo" / "acceptance")
        assert_acceptance_absent(destination)

    return destination
