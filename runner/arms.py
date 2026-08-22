"""Arm wiring: build the pieces, run the loop, write the controller log.

| Arm | Acceptance predicate location            | Meta-optimizer |
|-----|------------------------------------------|----------------|
| 1   | repo/acceptance/                         | disabled       |
| 2   | repo/acceptance/                         | enabled        |
| 3   | controller/predicate.py (repo's removed) | enabled        |

The controller runs in all three. Arms 1 and 2 log its verdict and ignore it;
arm 3 consumes it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONTROLLER_ROOT = Path(__file__).resolve().parent.parent.parent / "asi-controller"
if str(CONTROLLER_ROOT) not in sys.path:
    # The controller lives in a sibling repository. The runner is allowed to
    # see both; nothing under repo/ is.
    sys.path.insert(0, str(CONTROLLER_ROOT))

from controller.models import CandidateView, ControllerConfig  # noqa: E402
from controller.predicate import accept as controller_accept  # noqa: E402
from controller.predicate import explain as controller_explain  # noqa: E402
from controller.probe import ControllerProbe, PromptRegistry  # noqa: E402
from controller.reference.client import FrozenModelPin, ReferenceSolver  # noqa: E402
from controller.store.append_only import ControllerStore  # noqa: E402

from models.fake import FakeBackend, FakeClient  # noqa: E402
from repo.judge.sandbox import SubprocessSandbox  # noqa: E402
from repo.orchestrator.loop import LoopConfig, Orchestrator, WeakGate  # noqa: E402
from repo.prompts import load_prompt  # noqa: E402
from repo.schemas import Candidate, GroundingDocument  # noqa: E402
from shared.evalresult import EvalResult  # noqa: E402
from shared.manifest import ArmConfig, ModelSpec, RunManifest  # noqa: E402

# Placeholder model identities. The four roles and the family-separation
# constraint are settled; the concrete open models are an open decision, so the
# names below are explicitly marked UNRESOLVED rather than silently defaulted
# to something plausible.
PLACEHOLDER_MODELS = [
    ModelSpec(role="orchestrator", name="UNRESOLVED-orchestrator", family="UNRESOLVED-A", served_by="fake"),
    ModelSpec(role="challenger", name="UNRESOLVED-orchestrator", family="UNRESOLVED-A", served_by="fake"),
    ModelSpec(role="verifier", name="UNRESOLVED-orchestrator", family="UNRESOLVED-A", served_by="fake"),
    ModelSpec(role="analyzer", name="UNRESOLVED-orchestrator", family="UNRESOLVED-A", served_by="fake"),
    ModelSpec(role="implementer", name="UNRESOLVED-orchestrator", family="UNRESOLVED-A", served_by="fake"),
    ModelSpec(role="weak_solver", name="Qwen3.5-4B", family="Qwen", served_by="fake"),
    ModelSpec(role="strong_solver", name="UNRESOLVED-strong-coder", family="Qwen", served_by="fake"),
    ModelSpec(
        role="reference_solver",
        name="UNRESOLVED-reference",
        family="UNRESOLVED-C",
        weights_sha256=None,
        served_by="fake",
    ),
]


@dataclass
class ArmResult:
    arm: int
    manifest: RunManifest
    store_path: Path
    documents: int
    accepted_record_ids: list[str]
    candidates: int
    isolation_level: str


def build_arm_config(arm: int, step_budget: int, meta_iterations: int) -> ArmConfig:
    return ArmConfig(
        arm=arm,
        predicate_location="controller" if arm == 3 else "repo",
        predicate_variant="deployed_c1",
        shadow_variant="prose_s31",
        meta_optimizer_enabled=arm in (2, 3),
        step_budget_per_document=step_budget,
        meta_iterations=meta_iterations if arm in (2, 3) else 0,
        solver_prompts_editable=True,
        short_circuit_on_weak=True,
    )


def _fake_clients(backend: FakeBackend) -> dict[str, FakeClient]:
    return {spec.role: FakeClient(spec, backend) for spec in PLACEHOLDER_MODELS}


def _to_view(candidate: Candidate, meta_iteration: int) -> CandidateView:
    return CandidateView(
        candidate_id=candidate.candidate_id,
        record_id=candidate.record_id,
        round_index=candidate.round_index,
        meta_iteration=meta_iteration,
        problem_statement=candidate.output.problem_statement,
        signature=candidate.output.signature,
        reference_solution=candidate.output.reference_solution,
        visible_tests=list(candidate.output.visible_tests),
        challenger_prompt=candidate.challenger_prompt,
        challenger_system_revision=load_prompt("challenger").short_revision,
        verifier_prompt_revision=candidate.verifier_prompt_revision,
        claimed_prompt_hash=candidate.prompt_hash,
        claimed_rubric_provenance=candidate.rubric_provenance,
    )


def run_arm(
    arm: int,
    documents: list[GroundingDocument],
    output_dir: Path,
    run_id: str,
    created_at: str,
    corpus_descriptor: dict[str, Any],
    step_budget: int = 4,
    meta_iterations: int = 0,
    seed: int = 0,
    fuzz_inputs: int = 200,
    isolation_level: str = "structural",
) -> ArmResult:
    arm_config = build_arm_config(arm, step_budget, meta_iterations)
    # Salt is per-run, NOT per-arm: arms must see the same documents in the
    # same order producing the same candidates, so that any difference
    # between them is attributable to where the predicate lives.
    backend = FakeBackend(salt=run_id)
    clients = _fake_clients(backend)

    registry = PromptRegistry()
    registry.register(0, load_prompt("challenger").short_revision)

    probe = ControllerProbe(
        reference=ReferenceSolver(
            client=clients["reference_solver"],
            pin=FrozenModelPin(
                name="UNRESOLVED-reference",
                family="UNRESOLVED-C",
                revision="unresolved",
                weights_sha256="0" * 64,
            ),
            strict=False,  # no served weights to check against yet
        ),
        registry=registry,
        config=ControllerConfig(fuzz_inputs=fuzz_inputs),
    )

    store = ControllerStore(output_dir / f"controller_arm{arm}.jsonl")

    def controller_probe(candidate: Candidate) -> list[EvalResult]:
        raw = probe.run(_to_view(candidate, meta_iteration=0))
        return [EvalResult(**r) for r in raw]

    # Arm 3 decides on the controller; arms 1 and 2 on the in-repo predicate.
    if arm == 3:
        accept_fn = controller_accept
        decided_by = "controller"
        harness_accept = None
    else:
        from repo.acceptance import accept as repo_accept

        def accept_fn(results: list[EvalResult]) -> bool:
            return repo_accept(results, "deployed_c1")

        decided_by = "repo"
        harness_accept = repo_accept

    orchestrator = Orchestrator(
        challenger=clients["challenger"],
        verifier=clients["verifier"],
        weak_solver=clients["weak_solver"],
        strong_solver=clients["strong_solver"],
        sandbox=SubprocessSandbox(timeout_s=10.0),
        accept=accept_fn,
        controller_probe=controller_probe,
        config=LoopConfig(step_budget=step_budget, weak_gate=WeakGate()),
        decided_by=decided_by,
    )

    accepted_ids: list[str] = []
    candidate_count = 0

    for document in documents:
        result = orchestrator.run_document(document)
        if result.accepted:
            accepted_ids.append(document.record_id)

        for record in result.rounds:
            if record.candidate is None:
                continue  # malformed output: no candidate to key a store row on
            candidate_count += 1
            verdict = controller_explain(
                [r.model_dump() for r in record.controller_results]
            )
            consistency = next(
                (r for r in record.controller_results if r.name == "prompt_hash_consistency"),
                None,
            )
            store.append(
                run_id=run_id,
                arm=arm,
                meta_iteration=0,
                record_id=record.record_id,
                candidate_id=record.candidate.candidate_id,
                round_index=record.round_index,
                prompt_hash=record.candidate.prompt_hash,
                rubric_provenance=record.candidate.rubric_provenance,
                challenger_system_revision=load_prompt("challenger").short_revision,
                verifier_prompt_revision=record.candidate.verifier_prompt_revision,
                consistency_ok=bool(consistency and consistency.passed),
                consistency_flags=list(consistency.details.get("flags", [])) if consistency else [],
                controller_accepted=verdict.accepted,
                controller_reason=verdict.reason,
                harness_accepted=(
                    harness_accept(record.harness_results, "deployed_c1")
                    if harness_accept is not None
                    else None
                ),
                harness_predicate_variant="deployed_c1" if harness_accept else None,
                harness_shadow_accepted=(
                    harness_accept(record.harness_results, "prose_s31")
                    if harness_accept is not None
                    else None
                ),
                harness_shadow_variant="prose_s31" if harness_accept else None,
                decided_by=decided_by,
                results=[r.model_dump() for r in record.harness_results]
                + [r.model_dump() for r in record.controller_results],
            )

    manifest = RunManifest(
        run_id=run_id,
        created_at=created_at,
        arm_config=arm_config,
        models=PLACEHOLDER_MODELS,
        corpus=corpus_descriptor,
        seed=seed,
        source_document_order=[d.record_id for d in documents],
        accepted_record_ids=accepted_ids,
        notes={
            "backend": "deterministic fake (no GPU on this host)",
            "isolation_level": isolation_level,
            "controller_fuzz_inputs": fuzz_inputs,
            "controller_mutation_threshold": 0.70,
        },
    )
    (output_dir / f"manifest_arm{arm}.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )

    return ArmResult(
        arm=arm,
        manifest=manifest,
        store_path=store.path,
        documents=len(documents),
        accepted_record_ids=accepted_ids,
        candidates=candidate_count,
        isolation_level=isolation_level,
    )
