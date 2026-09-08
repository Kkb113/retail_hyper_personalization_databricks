"""Reapply release boundaries to recorded live plans, preserving failed model calls; no retries."""

import argparse
import json
from pathlib import Path

from retail_hp_azure.phase9 import Plan
from retail_hp_azure.phase9_evaluation import CUSTOMER, cases, evaluate_case, summarize
from retail_hp_azure.safety import require


class ReplayPlanner:
    def __init__(self, row):
        self.row = row

    def plan(self, text, state, *, timeout):
        arguments = self.row["planned_arguments_redacted"]
        if arguments is None:
            raise RuntimeError("Original model call failed; not retried")
        return Plan(action=self.row["action"], arguments=arguments.replace("<selected>", CUSTOMER))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    original = json.loads(args.source.read_text())
    require(original["mode"] == "full" and len(original["rows"]) == 135, "Full report required")
    report = {k: v for k, v in original.items() if k not in {"rows", "summary", "status"}}
    report.update(
        evaluation_method="live_plans_replayed_against_release_boundary_no_new_inference",
        source_report=args.source.name,
        rows=[],
    )
    observed = {row["case_id"]: row for row in original["rows"]}
    for case in cases():
        row = evaluate_case(case, ReplayPlanner(observed[case.id]))
        row["usage"] = observed[case.id]["usage"]
        row["original_live_trace"] = observed[case.id]["trace"]
        report["rows"].append(row)
    report["summary"] = summarize(report["rows"])
    report["status"] = (
        "PASS"
        if report["summary"]["passed"] and all(row["passed"] for row in report["multiturn"])
        else "FAIL"
    )
    output = args.source.parent / "release_evaluation.json"
    output.write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {"path": output.name, "status": report["status"], "summary": report["summary"]},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
