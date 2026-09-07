# Phase 5 review and research

## Decisions

The legacy recommendation stack mixed database access, UI orchestration and
ranking. Phase 5 extracts only the deterministic domain runtime. Its MLflow
wrapper receives typed tabular requests and returns recommendation rows, while
all data and trained estimators are packaged as immutable artifacts. This keeps
the Candidate portable and prevents network/database dependency during inference.

No retraining was justified: Phase 3 already sealed the selected model and Phase
4 produced the governed feature foundation. The source holdout decision is
preserved, so Candidate is the correct alias and Champion would be misleading.
Likewise, deploying a serving endpoint just to validate packaging would add idle
cost and belongs in Phase 6.

The Databricks runtime exposed four compatibility details that are now encoded in
the implementation: experiment paths must not collide with notebook directories;
Pandas 3 text columns are cast to portable object strings; Unity Catalog tag keys
use underscore-safe names; and registered-model ownership is updated through the
SDK instead of unsupported SQL. Unity Catalog returns alias names normalized to
lowercase, so inspection is case-insensitive.

## Reproducibility and limitations

The registered package declares MLflow 3.8.1, Python 3.12.3 and seven pinned
scientific dependencies. It also loaded under local MLflow 3.16.0 and produced
the exact golden hash, although that forward-compatibility run correctly warned
about version drift; deployment must use the recorded lock.

Ratings, holidays and a store-to-region inventory mapping were absent from the
sealed source. Their warm-ranker inputs use documented neutral defaults, and
regional inventory falls back to global availability. Currency remains
`UNSPECIFIED`. These are transparent POC limitations, not silently invented data.

The golden suite is deterministic and structurally representative, but four
cases are not a production-quality offline evaluation. Baseline metrics are
preserved from the selected release; future holdout, fairness/privacy, robustness
and business-owner approval remain prerequisites for Champion.

## Authoritative sources reviewed

- [Azure Databricks model logging and registration](https://learn.microsoft.com/en-us/azure/databricks/mlflow/models)
- [Azure Databricks custom model overview](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models)
- [Unity Catalog privileges reference](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/access-control/privileges-reference)
- [Serverless job environment dependencies](https://learn.microsoft.com/en-us/azure/databricks/compute/serverless/dependencies)
- [Databricks Jobs API environment contract](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/api/latest/jobs)
- [MLflow model signatures and input examples](https://mlflow.org/docs/latest/ml/model/signatures)

The sources support explicit signatures/examples, Unity Catalog registration and
least privilege, isolated serverless job environments with declared dependencies,
and validation before serving. The implementation deliberately stops before
online serving.
