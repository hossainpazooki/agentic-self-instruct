"""OpenAI-compatible HTTP client for a locally served vLLM endpoint.

Deferred path. This host has no discrete GPU (Intel UHD integrated), so nothing
in the test suite exercises this client; the suite runs against models.fake.
Serve each role separately and point ``base_url`` at it.

No closed API is called from here: ``base_url`` must be an explicitly
configured host, and there is no default pointing at a hosted provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from models.base import ModelDispatchError
from shared.manifest import ModelSpec


class VLLMClient:
    def __init__(self, spec: ModelSpec, base_url: str, timeout_s: float = 300.0) -> None:
        if not base_url:
            raise ValueError("base_url must be set explicitly; there is no hosted default")
        self.spec = spec
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def generate(
        self,
        system: str,
        user: str,
        n: int = 1,
        temperature: float = 1.0,
        max_tokens: int = 2048,
    ) -> list[str]:
        payload = {
            "model": self.spec.name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "n": n,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ModelDispatchError(f"{self.spec.role}: {exc}") from exc

        choices = body.get("choices", [])
        if len(choices) != n:
            raise ModelDispatchError(
                f"{self.spec.role}: asked for {n} samples, got {len(choices)}"
            )
        return [c["message"]["content"] for c in choices]
