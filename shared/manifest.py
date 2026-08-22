"""Run manifest: what ran, on which models, over which frozen record IDs.

The manifest is what a reader consults to decide whether two arms were actually
comparable. It therefore records the matched-N down-sample as a frozen list of
record IDs, not as a count -- a count can be re-derived after the fact to mean
whatever the author needs it to mean.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.hashing import json_hash

Arm = Literal[1, 2, 3]
PredicateVariant = Literal["deployed_c1", "prose_s31"]
Role = Literal[
    "orchestrator",
    "challenger",
    "weak_solver",
    "strong_solver",
    "analyzer",
    "implementer",
    "verifier",
    "reference_solver",
]


class ModelSpec(BaseModel):
    """One served model. Unresolved fields stay None and are reported as gaps.

    ``weights_sha256`` is the one field that must not be None for the reference
    solver in a run that reports controller verdicts: a frozen reference whose
    weights were never pinned is not frozen, it is merely unchanged so far.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Role
    name: str
    family: str
    revision: str | None = None
    weights_sha256: str | None = None
    served_by: str = "unset"
    sampling: dict[str, Any] = Field(default_factory=dict)


class FamilySeparationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    satisfied: bool
    families: dict[str, str]
    violations: list[str] = Field(default_factory=list)


def check_family_separation(models: list[ModelSpec]) -> FamilySeparationReport:
    """Family separation is a requirement, so it is checked, not assumed."""
    by_role = {m.role: m for m in models}
    families = {m.role: m.family for m in models}
    violations: list[str] = []

    orch = by_role.get("orchestrator")
    ref = by_role.get("reference_solver")
    strong = by_role.get("strong_solver")

    if orch is not None and orch.family.lower().startswith("qwen"):
        violations.append("orchestrator family must not be Qwen")
    if orch is not None and ref is not None and orch.family.lower() == ref.family.lower():
        violations.append("reference family must differ from orchestrator family")
    if strong is not None and ref is not None and strong.family.lower() == ref.family.lower():
        violations.append("reference family must differ from strong solver family")
    if ref is not None and ref.weights_sha256 is None:
        violations.append("reference solver weights_sha256 is unpinned")

    return FamilySeparationReport(satisfied=not violations, families=families, violations=violations)


class ArmConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    arm: Arm
    predicate_location: Literal["repo", "controller"]
    predicate_variant: PredicateVariant
    shadow_variant: PredicateVariant | None
    meta_optimizer_enabled: bool
    step_budget_per_document: int
    meta_iterations: int
    solver_prompts_editable: bool
    short_circuit_on_weak: bool = True


class RunManifest(BaseModel):
    """One arm's run. ``frozen_record_ids`` is the matched-N commitment."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    arm_config: ArmConfig
    models: list[ModelSpec]
    corpus: dict[str, Any]
    seed: int
    source_document_order: list[str]
    frozen_record_ids: list[str] = Field(default_factory=list)
    accepted_record_ids: list[str] = Field(default_factory=list)
    notes: dict[str, Any] = Field(default_factory=dict)

    @property
    def family_separation(self) -> FamilySeparationReport:
        return check_family_separation(self.models)

    def digest(self) -> str:
        return json_hash(self.model_dump(mode="json"))
