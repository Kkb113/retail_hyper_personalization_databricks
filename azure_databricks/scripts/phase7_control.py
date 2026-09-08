"""Plan, inspect, or apply the bounded Phase 7 operational-state foundation."""

import argparse
import json

from retail_hp_azure.phase2 import CloudContext
from retail_hp_azure.phase7_runtime import (
    apply_phase7,
    cost_plan,
    deploy_export_job,
    inspect_phase7,
    record_evidence,
    run_export_job,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "command", choices=("plan", "inspect", "apply", "deploy-export", "run-export", "audit")
)
args = parser.parse_args()

if args.command == "plan":
    report = cost_plan()
elif args.command in {"inspect", "audit"}:
    report = inspect_phase7(CloudContext())
    record_evidence(
        "cloud_preflight.json" if args.command == "inspect" else "cloud_reconciliation.json",
        report,
    )
elif args.command == "apply":
    report = apply_phase7(CloudContext(apply=True))
    record_evidence("acceptance.json", report)
elif args.command == "deploy-export":
    report = deploy_export_job(CloudContext(apply=True))
    record_evidence("export_deploy.json", report)
else:
    report = run_export_job(CloudContext(apply=True))
    record_evidence("export_run.json", report)
print(json.dumps(report, indent=2, sort_keys=True))
