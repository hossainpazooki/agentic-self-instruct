"""Canonical check names.

Both the harness and the controller import these. A typo becomes an
ImportError instead of a silently missing measurement that the predicate
would read as "check absent -> reject".
"""

from __future__ import annotations

# --- emitted inside repo/ (the agent's mutable harness) ---
CHALLENGER_WELLFORMED = "challenger_output_wellformed"
QUALITY_VERIFIER = "quality_verifier"
QUALITY_VERIFIER_FINAL = "quality_verifier_final"
WEAK_DISPATCH = "weak_solver_dispatch"
STRONG_DISPATCH = "strong_solver_dispatch"
WEAK_AVG = "weak_solver_avg"
WEAK_MAX = "weak_solver_max"
WEAK_MIN = "weak_solver_min"
STRONG_AVG = "strong_solver_avg"
SOLVER_GAP = "solver_gap"

# --- emitted by the controller (trusted, outside the edit surface) ---
PROMPT_HASH_CONSISTENCY = "prompt_hash_consistency"
DIFFERENTIAL_FUZZ = "differential_fuzz"
MUTATION_SCORE = "mutation_score"
REFERENCE_DISPATCH = "reference_solver_dispatch"

HARNESS_CHECKS = frozenset(
    {
        CHALLENGER_WELLFORMED,
        QUALITY_VERIFIER,
        QUALITY_VERIFIER_FINAL,
        WEAK_DISPATCH,
        STRONG_DISPATCH,
        WEAK_AVG,
        WEAK_MAX,
        WEAK_MIN,
        STRONG_AVG,
        SOLVER_GAP,
    }
)

CONTROLLER_CHECKS = frozenset(
    {
        PROMPT_HASH_CONSISTENCY,
        DIFFERENTIAL_FUZZ,
        MUTATION_SCORE,
        REFERENCE_DISPATCH,
    }
)
