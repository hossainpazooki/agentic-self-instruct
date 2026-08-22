"""Deterministic fake model backend.

TEST SCAFFOLDING. It exists because this host has no discrete GPU, so the
build, the unit tests, the isolation test, and the three-arm smoke all have to
run without served weights. It is deterministic by construction: every decision
is a hash of (role, salt, prompt-derived key), never a random draw, so a rerun
reproduces a run exactly.

It is not a language model and does not pretend to be one. It reads the
identifiers that a real prompt already carries -- ``RECORD_ID:`` and the
function signature -- and returns real, executable Python from models.tasklib.
That is enough to exercise every downstream component for real: the sandbox
executes it, the fuzzer differentiates it, the mutation runner mutates it.

What it CANNOT tell you: anything about prompt quality, refinement behaviour,
or whether the meta-optimizer's edits help. Those need real models.
"""

from __future__ import annotations

import hashlib
import json
import re

from models.base import ModelDispatchError
from models.tasklib import POOR_SOLUTIONS, TASKS, TASKS_BY_KEY, Task
from shared.manifest import ModelSpec

RECORD_ID_RE = re.compile(r"RECORD_ID:\s*(\S+)")
SIGNATURE_RE = re.compile(r"^\s*def\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)


def _digest(*parts: str) -> int:
    joined = "\x1f".join(parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(joined).digest()[:8], "big")


def _fraction(*parts: str) -> float:
    return (_digest(*parts) % 10_000) / 10_000.0


class FakeBackend:
    """Shared deterministic state across all fake role clients in one run."""

    def __init__(
        self,
        salt: str = "asi",
        weak_correct_rate: float = 0.10,
        weak_wrong_rate: float = 0.30,
        strong_correct_rate: float = 0.80,
        verifier_reject_rate: float = 0.10,
        flaw_weights: tuple[float, float, float, float] = (0.55, 0.20, 0.15, 0.10),
    ) -> None:
        self.salt = salt
        self.weak_correct_rate = weak_correct_rate
        self.weak_wrong_rate = weak_wrong_rate
        self.strong_correct_rate = strong_correct_rate
        self.verifier_reject_rate = verifier_reject_rate
        self.flaw_weights = flaw_weights

    # --- challenger -------------------------------------------------------

    def task_for(self, record_id: str, round_index: int) -> Task:
        """Each refinement round moves to a different task -- the paper's
        'ENTIRELY NEW question from a DIFFERENT angle', mechanised."""
        index = _digest(self.salt, "task", record_id, str(round_index)) % len(TASKS)
        return TASKS[index]

    def flaw_for(self, record_id: str, round_index: int) -> str:
        roll = _fraction(self.salt, "flaw", record_id, str(round_index))
        cumulative = 0.0
        for mode, weight in zip(("sound", "weak_tests", "wrong_ref", "hardcoded"), self.flaw_weights):
            cumulative += weight
            if roll < cumulative:
                return mode
        return "sound"

    def challenger_output(self, record_id: str, round_index: int) -> dict:
        task = self.task_for(record_id, round_index)
        flaw = self.flaw_for(record_id, round_index)

        solution = task.correct_solution
        tests = list(task.strong_tests)
        if flaw == "weak_tests":
            tests = list(task.weak_tests)
        elif flaw == "wrong_ref":
            solution = task.wrong_solution
        elif flaw == "hardcoded":
            solution = task.hardcoded_solution
            tests = list(task.weak_tests)

        return {
            "problem_statement": task.problem_statement,
            "signature": task.signature,
            "reference_solution": solution,
            "visible_tests": tests,
            "_fake_task_key": task.key,
            "_fake_flaw_mode": flaw,
        }

    # --- solvers ----------------------------------------------------------

    def solver_solution(self, task: Task, solver: str, sample_index: int, key: str) -> str:
        if solver == "reference":
            # The controller's reference solver is a frozen third family and is
            # always competent; a reference that is itself unreliable would make
            # differential fuzzing meaningless.
            return task.correct_solution

        roll = _fraction(self.salt, solver, key, task.key, str(sample_index))

        if solver == "strong":
            return task.correct_solution if roll < self.strong_correct_rate else task.wrong_solution

        # Weak solver: correct / subtly wrong / simply poor.
        if roll < self.weak_correct_rate:
            return task.correct_solution
        if roll < self.weak_correct_rate + self.weak_wrong_rate:
            return task.wrong_solution
        return POOR_SOLUTIONS.get(task.key, task.wrong_solution)


class FakeClient:
    """One role's view of the shared backend."""

    def __init__(self, spec: ModelSpec, backend: FakeBackend) -> None:
        self.spec = spec
        self.backend = backend

    def generate(
        self,
        system: str,
        user: str,
        n: int = 1,
        temperature: float = 1.0,
        max_tokens: int = 2048,
    ) -> list[str]:
        role = self.spec.role
        if role == "challenger":
            return [self._challenger(user) for _ in range(n)]
        if role in ("weak_solver", "strong_solver", "reference_solver"):
            solver = {"weak_solver": "weak", "strong_solver": "strong", "reference_solver": "reference"}[role]
            return [self._solver(user, solver, i) for i in range(n)]
        if role == "verifier":
            return [self._verifier(user) for _ in range(n)]
        if role == "analyzer":
            return [self._analyzer(user) for _ in range(n)]
        if role == "implementer":
            return [self._implementer(user, i) for i in range(n)]
        if role == "orchestrator":
            return ["ACK"] * n
        raise ModelDispatchError(f"fake backend has no behaviour for role {role!r}")

    # --- per-role behaviour ----------------------------------------------

    def _record_id(self, user: str) -> str:
        match = RECORD_ID_RE.search(user)
        if match is None:
            raise ModelDispatchError("fake challenger: prompt carries no RECORD_ID")
        return match.group(1)

    def _round_index(self, user: str) -> int:
        match = re.search(r"ROUND:\s*(\d+)", user)
        return int(match.group(1)) if match else 0

    def _challenger(self, user: str) -> str:
        payload = self.backend.challenger_output(self._record_id(user), self._round_index(user))
        return json.dumps(payload, indent=2)

    def _task_from_signature(self, user: str) -> Task:
        match = SIGNATURE_RE.search(user)
        if match is None:
            raise ModelDispatchError("fake solver: prompt carries no function signature")
        name = match.group(1)
        task = TASKS_BY_KEY.get(name)
        if task is None:
            raise ModelDispatchError(f"fake solver: no task for function {name!r}")
        return task

    def _solver(self, user: str, solver: str, sample_index: int) -> str:
        task = self._task_from_signature(user)
        key = RECORD_ID_RE.search(user)
        return self.backend.solver_solution(
            task, solver, sample_index, key.group(1) if key else task.key
        )

    def _verifier(self, user: str) -> str:
        """Mirrors the four-check output format of Appendix C.1 Figure 9."""
        try:
            task = self._task_from_signature(user)
            key = task.key
        except ModelDispatchError:
            key = "unknown"
        test_count = user.count("assert ")
        roll = _fraction(self.backend.salt, "qv", key, str(test_count))

        # The verifier is an LLM check, so it is fallible on purpose. It does
        # reliably catch a suite that is too small, which is the one failure
        # mode a rubric-shaped verifier is genuinely good at.
        too_few = test_count < 5
        rejects = too_few or roll < self.backend.verifier_reject_rate
        verdict = "FAIL" if rejects else "PASS"
        issues = []
        if too_few:
            issues.append(f"only {test_count} visible tests; minimum is 5")
        if rejects and not too_few:
            issues.append("problem statement risks leaking the approach")

        return (
            "CHECK_1_VERDICT: NO_LEAKAGE\n"
            f"CHECK_2_VERDICT: {'TOO_EASY' if rejects else 'GOOD'}\n"
            f"CHECK_3_VERDICT: {'FAIL' if too_few else 'PASS'}\n"
            f"CHECK_3_ISSUES: {'; '.join(issues) if issues else 'none'}\n"
            "CHECK_4_VERDICT: CONSISTENT\n"
            f"OVERALL: {verdict}\n"
            f"FEEDBACK: {'; '.join(issues) if issues else 'none'}"
        )

    def _analyzer(self, user: str) -> str:
        return (
            "ROOT CAUSE: accepted problems cluster on shallow edge-case checks; "
            "the weak solver recovers most tests by pattern-matching the signature. "
            "RECOMMENDATION: require at least one test whose expected value cannot "
            "be inferred from the signature alone."
        )

    def _implementer(self, user: str, sample_index: int) -> str:
        """Returns a unified diff against the harness.

        The adversarial variant used by the isolation test lives in
        runner/adversarial.py, not here -- this one stays inside repo/.
        """
        return (
            "--- a/repo/challenger/prompt.md\n"
            "+++ b/repo/challenger/prompt.md\n"
            "@@\n"
            "-Between 5 and 12 asserts, each a single self-contained\n"
            "+Between 6 and 12 asserts, each a single self-contained\n"
        )
