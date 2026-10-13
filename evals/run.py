"""Eval runner.

Usage:
  python -m evals.run                 # run full set
  python -m evals.run --category tool # one slice
  python -m evals.run --no-judges     # skip LLM judges (fast, string checks only)
"""
from __future__ import annotations
import argparse
import asyncio
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import yaml

from app.agent.graph import answer as run_agent
from app.identity import resolve
from evals.judges import judge_abstention, judge_faithfulness


ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases" / "golden.yaml"
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    string_pass: bool
    retrieval_pass: bool
    tool_pass: bool
    faithfulness: float | None
    abstention_correct: bool | None
    latency_ms: int
    failures: list[str] = field(default_factory=list)


def _check_strings(answer: str, expected: dict) -> list[str]:
    failures = []
    for s in expected.get("must_contain", []):
        if s.lower() not in answer.lower():
            failures.append(f"must_contain missing: {s!r}")
    any_opts = expected.get("must_contain_any")
    if any_opts and not any(s.lower() in answer.lower() for s in any_opts):
        failures.append(f"must_contain_any missed all: {any_opts}")
    for s in expected.get("must_not_contain", []):
        if s.lower() in answer.lower():
            failures.append(f"must_not_contain present: {s!r}")
    return failures


def _check_citations(trace, expected: dict) -> bool:
    want = expected.get("expected_citations")
    if not want:
        return True
    got_docs = {c.document_id for c in trace.citations}
    return all(w["document_id"] in got_docs for w in want)


def _check_tool(trace, expected: dict) -> bool:
    want = expected.get("expected_tool")
    if not want:
        return True
    return any(tc.name == want for tc in trace.tool_calls)


async def _run_case(case: dict, use_judges: bool) -> CaseResult:
    user = await resolve(case["user"]["slack_user_id"])
    trace = await run_agent(user, case["question"])

    expected = case["expected"]
    str_fails = _check_strings(trace.answer, expected)
    retrieval_ok = _check_citations(trace, expected)
    tool_ok = _check_tool(trace, expected)

    faith = None
    abst_ok = None
    if use_judges:
        context = "\n\n".join(f"[{c.document_id}#{c.section}] {c.content}" for c in trace.retrieved)
        f = await judge_faithfulness(case["question"], context or "(none)", trace.answer)
        faith = f.get("faithfulness")

        a = await judge_abstention(
            case["question"],
            trace.answer,
            answerable=expected.get("answerable", True),
            should_escalate=expected.get("should_escalate", False),
        )
        if a.get("correctness") is not None:
            abst_ok = a["correctness"] == "CORRECT"

    return CaseResult(
        id=case["id"],
        category=case["category"],
        question=case["question"],
        string_pass=not str_fails,
        retrieval_pass=retrieval_ok,
        tool_pass=tool_ok,
        faithfulness=faith,
        abstention_correct=abst_ok,
        latency_ms=trace.latency_ms,
        failures=str_fails + ([] if retrieval_ok else ["citation mismatch"])
                           + ([] if tool_ok else ["tool mismatch"]),
    )


def _aggregate(results: list[CaseResult]) -> dict:
    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    def passed(r: CaseResult) -> bool:
        return r.string_pass and r.retrieval_pass and r.tool_pass

    out = {"by_category": {}, "overall": {}}
    for cat, rs in by_cat.items():
        faiths = [r.faithfulness for r in rs if r.faithfulness is not None]
        out["by_category"][cat] = {
            "n": len(rs),
            "pass_rate": sum(passed(r) for r in rs) / len(rs),
            "faithfulness_mean": statistics.mean(faiths) if faiths else None,
            "latency_p50": statistics.median(r.latency_ms for r in rs),
            "latency_p95": sorted(r.latency_ms for r in rs)[int(0.95 * (len(rs) - 1))],
        }
    all_faiths = [r.faithfulness for r in results if r.faithfulness is not None]
    out["overall"] = {
        "n": len(results),
        "pass_rate": sum(passed(r) for r in results) / len(results),
        "faithfulness_mean": statistics.mean(all_faiths) if all_faiths else None,
        "latency_p50": statistics.median(r.latency_ms for r in results),
        "latency_p95": sorted(r.latency_ms for r in results)[int(0.95 * (len(results) - 1))],
    }
    return out


def _render_markdown(agg: dict) -> str:
    lines = ["| Category | N | Pass% | Faithfulness | p50 (ms) | p95 (ms) |",
             "|---|---|---|---|---|---|"]
    for cat, s in agg["by_category"].items():
        faith = f"{s['faithfulness_mean']:.2f}" if s["faithfulness_mean"] is not None else "—"
        lines.append(f"| {cat} | {s['n']} | {s['pass_rate']*100:.0f}% | {faith} | {s['latency_p50']} | {s['latency_p95']} |")
    o = agg["overall"]
    faith = f"{o['faithfulness_mean']:.2f}" if o["faithfulness_mean"] is not None else "—"
    lines.append(f"| **Overall** | **{o['n']}** | **{o['pass_rate']*100:.0f}%** | **{faith}** | **{o['latency_p50']}** | **{o['latency_p95']}** |")
    return "\n".join(lines)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", help="run only this category")
    ap.add_argument("--no-judges", action="store_true", help="skip LLM judges")
    args = ap.parse_args()

    cases = yaml.safe_load(CASES.read_text())["cases"]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if not cases:
        print("No cases to run", file=sys.stderr)
        return

    print(f"Running {len(cases)} cases (judges={'off' if args.no_judges else 'on'})...")
    results = []
    for case in cases:
        try:
            r = await _run_case(case, use_judges=not args.no_judges)
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {case['id']} crashed: {e}")
            continue
        mark = "✓" if (r.string_pass and r.retrieval_pass and r.tool_pass) else "✗"
        print(f"  {mark} {r.id} ({r.latency_ms}ms)")
        if r.failures:
            for f in r.failures:
                print(f"       - {f}")
        results.append(r)

    agg = _aggregate(results)
    md = _render_markdown(agg)
    print("\n" + md)

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    out_path = RESULTS / f"{stamp}.json"
    out_path.write_text(json.dumps({
        "aggregate": agg,
        "cases": [asdict(r) for r in results],
    }, indent=2, default=str))
    (RESULTS / "latest.json").write_text(out_path.read_text())
    (RESULTS / "latest.md").write_text(md)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
