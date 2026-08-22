"""Three-arm smoke run over a small document set.

Produces, per arm: a run manifest, an append-only controller log, and a
got-away rate computable from that log alone. Runs against the deterministic
fake backend, because this host has no discrete GPU.

What a green smoke run does and does not show:

  DOES  the wiring holds end to end -- candidates are generated, executed,
        scored, judged by both predicates, and logged; the controller runs on
        every candidate in every arm; matched-N freezes reproducibly.
  DOES NOT  say anything about data quality, prompt quality, or whether the
        meta-optimizer helps. Those need real models. A smoke got-away rate is
        a property of models/tasklib.py's flaw mix, not of Agentic
        Self-Instruct.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# runner.arms puts the sibling controller repository on sys.path. Import it
# before anything under `controller.` is reachable.
from corpus.codesearchnet import load_documents  # noqa: E402
from runner.arms import CONTROLLER_ROOT, run_arm  # noqa: E402

from controller.store.append_only import ControllerStore, got_away_rate_by_iteration  # noqa: E402
from runner.isolation import enforce  # noqa: E402
from runner.matched_n import freeze  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Three-arm smoke run")
    parser.add_argument("--documents", type=int, default=20)
    parser.add_argument("--step-budget", type=int, default=4)
    parser.add_argument("--fuzz-inputs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "runs" / "smoke")
    parser.add_argument(
        "--apply-permissions",
        action="store_true",
        help="drop write access on the controller repo (leaves the tree read-only)",
    )
    args = parser.parse_args(argv)

    args.output.mkdir(parents=True, exist_ok=True)
    run_id = f"smoke-{args.seed}"
    created_at = datetime.now(timezone.utc).isoformat()

    isolation = enforce(
        controller_root=CONTROLLER_ROOT,
        harness_root=REPO_ROOT,
        apply_permissions=args.apply_permissions,
    )
    print(f"isolation: level={isolation.level} structural={isolation.structural_ok} "
          f"imports_clean={isolation.imports_clean}")
    for note in isolation.notes:
        print(f"  note: {note}")

    documents, corpus = load_documents(limit=args.documents)
    print(f"corpus: {corpus['name']} synthetic={corpus['synthetic']} n={len(documents)}")
    if corpus["synthetic"]:
        print("  WARNING: synthetic placeholder documents; this is not a CodeSearchNet run.")

    results = {}
    for arm in (1, 2, 3):
        result = run_arm(
            arm=arm,
            documents=documents,
            output_dir=args.output,
            run_id=run_id,
            created_at=created_at,
            corpus_descriptor=corpus,
            step_budget=args.step_budget,
            meta_iterations=0,
            seed=args.seed,
            fuzz_inputs=args.fuzz_inputs,
            isolation_level=isolation.level,
        )
        results[arm] = result
        print(
            f"arm {arm}: documents={result.documents} candidates={result.candidates} "
            f"accepted_docs={len(result.accepted_record_ids)}"
        )

    matched = freeze(
        {arm: r.accepted_record_ids for arm, r in results.items()},
        args.output / "matched_n.json",
        seed=str(args.seed),
    )
    print(f"matched-N: {matched['matched_n']} (sizes {matched['accepted_sizes']}) "
          f"digest={matched['digest'][:12]}")

    print("\ngot-away rates (from the controller log alone):")
    summary = {}
    for arm, result in results.items():
        entries = ControllerStore(result.store_path).read()
        by_iteration = got_away_rate_by_iteration(entries)
        summary[arm] = by_iteration
        for iteration, bucket in by_iteration.items():
            rate = bucket["got_away_rate"]
            rate_text = "n/a (harness made no decisions)" if rate is None else f"{rate:.3f}"
            print(
                f"  arm {arm} iter {iteration}: candidates={bucket['candidates']} "
                f"harness_accepted={bucket['harness_accepted']} "
                f"controller_accepted={bucket['controller_accepted']} "
                f"got_away={bucket['got_away']} rate={rate_text}"
            )

    (args.output / "got_away_summary.json").write_text(
        json.dumps({str(a): s for a, s in summary.items()}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
