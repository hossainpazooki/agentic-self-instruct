"""Matched-N down-sampling, frozen once.

The spec: down-sampling is done once, before any training, and the frozen ID
lists are committed. Two properties make that meaningful:

* **Deterministic** -- selection is a sort by ``sha256(seed || record_id)``,
  not a shuffle, so the same inputs give the same lists on any platform and
  Python version, and the frozen list can be regenerated and diffed rather
  than merely trusted.
* **Committed** -- the lists go to disk with the seed and the digest recorded
  alongside them, so a later run that quietly re-samples is detectable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _rank(seed: str, record_id: str) -> str:
    return hashlib.sha256(f"{seed}\x1f{record_id}".encode("utf-8")).hexdigest()


def downsample(record_ids: list[str], n: int, seed: str) -> list[str]:
    """Deterministically select ``n`` ids, returned in stable rank order."""
    if n >= len(record_ids):
        return sorted(record_ids)
    ranked = sorted(record_ids, key=lambda rid: _rank(seed, rid))
    return sorted(ranked[:n])


def freeze(
    accepted_by_arm: dict[int, list[str]],
    output_path: Path,
    seed: str,
) -> dict[str, Any]:
    """Compute matched-N across arms and write the frozen lists.

    N is the smallest accepted-set size across arms. If any arm accepted
    nothing, N is 0 and that is recorded plainly -- a matched-N of zero is a
    result about the arms, not an error to paper over.
    """
    sizes = {arm: len(ids) for arm, ids in accepted_by_arm.items()}
    n = min(sizes.values()) if sizes else 0

    frozen = {arm: downsample(ids, n, f"{seed}:arm{arm}") for arm, ids in accepted_by_arm.items()}
    payload = {
        "seed": seed,
        "matched_n": n,
        "accepted_sizes": sizes,
        "frozen_record_ids": {str(arm): ids for arm, ids in frozen.items()},
        "digest": hashlib.sha256(
            json.dumps({str(a): i for a, i in frozen.items()}, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload
