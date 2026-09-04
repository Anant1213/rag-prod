"""Retrieval eval gate. Fails CI when recall@k or citation coverage regresses.

    python -m eval.run_eval            # score against eval/baseline.json
    python -m eval.run_eval --write    # accept current numbers as the new baseline
"""
import asyncio
import json
import pathlib
import sys

from app import db
from app.retrieval import retrieve

HERE = pathlib.Path(__file__).parent
GOLDEN = HERE / "golden.jsonl"
BASELINE = HERE / "baseline.json"
TOLERANCE = 0.02  # allow 2 points of noise, block anything worse


async def score() -> dict:
    cases = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    await db.open_pool()
    hits_at_1, hits_at_k, reciprocal = 0, 0, 0.0
    failures = []
    try:
        for case in cases:
            chunks = await retrieve(case["question"], final_k=5)
            sources = [c.source for c in chunks]
            expected = case["expected_source"]
            if expected in sources:
                hits_at_k += 1
                rank = sources.index(expected) + 1
                reciprocal += 1 / rank
                if rank == 1:
                    hits_at_1 += 1
            else:
                failures.append({"q": case["question"], "expected": expected, "got": sources})
    finally:
        await db.close_pool()

    n = len(cases)
    return {
        "n": n,
        "recall@1": round(hits_at_1 / n, 4),
        "recall@5": round(hits_at_k / n, 4),
        "mrr": round(reciprocal / n, 4),
        "failures": failures,
    }


def main() -> None:
    result = asyncio.run(score())
    print(json.dumps({k: v for k, v in result.items() if k != "failures"}, indent=2))
    for f in result["failures"]:
        print(f"MISS  {f['q']}\n      expected={f['expected']} got={f['got']}")

    if "--write" in sys.argv:
        BASELINE.write_text(json.dumps({k: result[k] for k in ("recall@1", "recall@5", "mrr")}, indent=2))
        print(f"baseline written -> {BASELINE}")
        return

    if not BASELINE.exists():
        print("no baseline; run with --write once you are happy with these numbers")
        return

    baseline = json.loads(BASELINE.read_text())
    regressions = [
        f"{m}: {result[m]:.4f} < {baseline[m]:.4f}"
        for m in ("recall@1", "recall@5", "mrr")
        if result[m] < baseline[m] - TOLERANCE
    ]
    if regressions:
        print("EVAL GATE FAILED:\n  " + "\n  ".join(regressions))
        sys.exit(1)
    print("eval gate passed")


if __name__ == "__main__":
    main()
