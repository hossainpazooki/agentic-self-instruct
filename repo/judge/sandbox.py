"""Sandboxed execution of candidate solutions against visible tests.

This replaces the paper's Kimi rubric judge. Documented departure: the paper
scores an open-ended answer against an LLM-graded weighted rubric; here the
score is the fraction of visible tests a solution passes, executed rather than
judged. See docs/fidelity.md.

Two implementations behind one interface:

``SubprocessSandbox``  timeout + separate interpreter. Adequate for the fake
                       backend and the test suite. NOT a security boundary --
                       generated code runs as the calling user with a live
                       filesystem. Never point it at model-generated code from
                       a real run.
``DockerSandbox``      ``--network=none``, read-only root, non-root user,
                       tmpfs workdir, cpu/memory caps, hard timeout. This is
                       the one to use once real models are generating code.

A timeout is not an exception. It is a blocking EvalResult with passed=False,
because a candidate that vanishes into a traceback is a missing row in the
got-away audit, and a missing row reads as "nothing happened".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

RUNNER_SOURCE = r'''
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
solution = payload["solution"]
tests = payload["tests"]

outcomes = []
namespace = {}

try:
    exec(compile(solution, "<solution>", "exec"), namespace)
except BaseException as exc:  # noqa: BLE001 - report, never propagate
    print(json.dumps({
        "load_error": f"{type(exc).__name__}: {exc}",
        "outcomes": [],
    }))
    sys.exit(0)

for index, test in enumerate(tests):
    try:
        exec(compile(test, f"<test{index}>", "exec"), dict(namespace))
    except BaseException as exc:  # noqa: BLE001
        outcomes.append({"index": index, "passed": False,
                         "error": f"{type(exc).__name__}: {exc}"})
    else:
        outcomes.append({"index": index, "passed": True, "error": None})

print(json.dumps({"load_error": None, "outcomes": outcomes}))
'''


class TestOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    passed: bool
    error: str | None = None


class SandboxResult(BaseModel):
    """What happened. ``ok`` distinguishes infrastructure failure from a low score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcomes: list[TestOutcome] = Field(default_factory=list)
    timed_out: bool = False
    load_error: str | None = None
    runner_error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the sandbox itself worked, whatever the solution scored.

        A solution that fails to import is *not* a sandbox failure -- it is a
        legitimately worthless solution scoring 0. A timeout or a crashed
        runner is a sandbox failure.
        """
        return not self.timed_out and self.runner_error is None

    @property
    def tests_total(self) -> int:
        return len(self.outcomes)

    @property
    def tests_passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    @property
    def score(self) -> float:
        """Fraction of visible tests passed. No tests means no evidence: 0.0."""
        if not self.outcomes:
            return 0.0
        return self.tests_passed / len(self.outcomes)


class Sandbox(Protocol):
    def run(self, solution: str, tests: list[str]) -> SandboxResult: ...


class SubprocessSandbox:
    """Separate interpreter with a hard timeout. Not a security boundary."""

    def __init__(self, timeout_s: float = 10.0) -> None:
        self.timeout_s = timeout_s

    def run(self, solution: str, tests: list[str]) -> SandboxResult:
        workdir = tempfile.mkdtemp(prefix="asi-sandbox-")
        try:
            payload_path = os.path.join(workdir, "payload.json")
            runner_path = os.path.join(workdir, "runner.py")
            with open(payload_path, "w", encoding="utf-8") as fh:
                json.dump({"solution": solution, "tests": list(tests)}, fh)
            with open(runner_path, "w", encoding="utf-8") as fh:
                fh.write(RUNNER_SOURCE)

            try:
                completed = subprocess.run(
                    [sys.executable, "-I", "-S", runner_path, payload_path],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=workdir,
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(timed_out=True)

            return _parse_runner_output(completed.returncode, completed.stdout, completed.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


class DockerSandbox:
    """Network-isolated container. The path to use once real models generate code."""

    def __init__(
        self,
        image: str = "python:3.12-slim",
        timeout_s: float = 10.0,
        memory: str = "512m",
        cpus: str = "1.0",
    ) -> None:
        self.image = image
        self.timeout_s = timeout_s
        self.memory = memory
        self.cpus = cpus

    def run(self, solution: str, tests: list[str]) -> SandboxResult:
        workdir = tempfile.mkdtemp(prefix="asi-docker-")
        try:
            with open(os.path.join(workdir, "payload.json"), "w", encoding="utf-8") as fh:
                json.dump({"solution": solution, "tests": list(tests)}, fh)
            with open(os.path.join(workdir, "runner.py"), "w", encoding="utf-8") as fh:
                fh.write(RUNNER_SOURCE)

            command = [
                "docker", "run", "--rm",
                "--network=none",
                "--read-only",
                "--user", "65534:65534",
                f"--memory={self.memory}",
                f"--cpus={self.cpus}",
                "--pids-limit", "128",
                "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
                "-v", f"{workdir}:/work:ro",
                "-w", "/work",
                self.image,
                "python", "-I", "-S", "/work/runner.py", "/work/payload.json",
            ]
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True, timeout=self.timeout_s + 20.0
                )
            except subprocess.TimeoutExpired:
                return SandboxResult(timed_out=True)
            except FileNotFoundError:
                return SandboxResult(runner_error="docker executable not found")

            return _parse_runner_output(completed.returncode, completed.stdout, completed.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _parse_runner_output(returncode: int, stdout: str, stderr: str) -> SandboxResult:
    if returncode != 0 and not stdout.strip():
        return SandboxResult(runner_error=f"exit {returncode}: {stderr.strip()[:400]}")
    try:
        parsed = json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return SandboxResult(runner_error=f"unparseable runner output: {stdout.strip()[:400]}")

    return SandboxResult(
        outcomes=[TestOutcome(**o) for o in parsed.get("outcomes", [])],
        load_error=parsed.get("load_error"),
    )
