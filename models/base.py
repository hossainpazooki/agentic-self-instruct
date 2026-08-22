"""Model client protocol.

Every LLM role in the system goes through this one interface so that swapping
a deterministic fake for a served vLLM endpoint is a config change, not a code
change. The build and its tests run against the fake; the smoke run needs real
weights behind the same protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.manifest import ModelSpec


@runtime_checkable
class ModelClient(Protocol):
    spec: ModelSpec

    def generate(
        self,
        system: str,
        user: str,
        n: int = 1,
        temperature: float = 1.0,
        max_tokens: int = 2048,
    ) -> list[str]:
        """Return ``n`` samples. Must return exactly ``n`` items or raise."""
        ...


class ModelDispatchError(RuntimeError):
    """Raised by a client when generation fails.

    Callers convert this into a blocking EvalResult rather than letting it
    propagate: a solver failure that escapes as an exception silently removes
    the candidate from the got-away audit.
    """
