# Phase 1 research and decisions

Researched 2026-09-04 using primary documentation. These are engineering choices
for an occasional synthetic POC, not production or cost guarantees.

## Databricks foundation

1. [Bundle configuration reference](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/reference):
   use one target, explicit workspace root and parameters. Resource definitions
   are deliberately empty in Phase 1; naming variables do not create anything.
2. [Bundle CLI commands](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/cli/bundle-commands):
   strict validation and resolved JSON check syntax and target configuration.
   Plan previews changes but can build artifacts; hence our source guard forbids
   build/script hooks before invoking it. Deploy is not exposed by our helper.
3. [Direct deployment engine](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/direct):
   explicitly select the modern direct engine and pin CLI version, avoiding
   implicit engine changes and a Terraform download for this resource-free foundation.
   This is not a migration of existing deployed bundle state.
4. [Bundle variables](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/variables):
   environment variables, command-line values and local override files can change
   deployment parameters. Reject them in the guarded workflow; verify resolved
   values as well as source YAML. The legacy catalog cannot be selected.
5. [Sharing bundle files](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/sharing):
   parent sync paths can expand the upload boundary. Do not sync parent directories,
   frozen assets, local environments or secret files. Phase 1 allows only a fixed
   harmless marker to be eligible for future sync; validation/plan never upload it.
6. [Azure CLI authentication](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/auth/azure-cli):
   reuse interactive Azure CLI auth for local read-only work, with no committed PAT.
   Match subscription/tenant fingerprints before any scoped resource reads.
7. [Official CLI release](https://github.com/databricks/cli/releases/tag/v1.15.0):
   pin version 1.15.0 and verify the distribution ZIP against official SHA-256
   checksums. Platform hashes are recorded in `config/toolchain.json`.

## Reproducibility and testing

8. [Python src layout](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/):
   use explicit package discovery and test installed wheels in isolated Python
   mode, preventing accidental reliance on working-directory legacy modules.
9. [uv lock compilation](https://docs.astral.sh/uv/pip/compile/):
   produce platform-universal, hash-pinned foundation locks for jobs, serving,
   agent and app. Test each clean environment rather than assuming lock resolution
   proves compatibility. ML inference pins are a later model-parity deliverable.
10. [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/):
    reject coercions and extra fields; additionally enforce exact literal types
    for boolean/numeric safety settings. Publish a generated JSON Schema and test
    its parity with the actual configuration.
11. [FastAPI testing](https://fastapi.tiangolo.com/tutorial/testing/):
    exercise local health/readiness/version contracts in process. Readiness stays
    503 until actual model and agent prerequisites exist.

## Budget and idle constraints

12. [Azure budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets):
    alerts are not resource shutdown. Cost data can lag 8–24 hours and budget
    evaluation is periodic. Record the target, reserve and control deployment
    status honestly; test delivery later to both privately supplied recipients.
13. [Azure spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit):
    do not promise a custom INR 12,000 PAYG resource-group hard ceiling. The
    internal INR 9,000 stop target is a future best-effort safeguard, not a cap.
14. [Custom model serving](https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models):
    native scale-to-zero waits 30 minutes. Keep serving disabled under the owner's
    stricter 20-minute human-idle requirement; evaluate triggered/batch inference
    before adding a paid real-time endpoint.
15. [SQL warehouse creation](https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/create):
    use the one-minute API auto-stop setting if a warehouse is later approved.
    This is not a global shutdown setting for jobs, apps or serving endpoints.
16. [Databricks Apps](https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/):
    a running app is a separate compute cost. The local skeleton costs no Azure
    compute; deploy no app until its session/idle/manual stop controls are tested.

## Conclusion

Build-tool security follow-up: the repository's pre-existing pins were affected
by [setuptools path traversal](https://github.com/pypa/setuptools/security/advisories/GHSA-5rjg-fvgr-3xxf),
[wheel unpack path traversal](https://github.com/pypa/wheel/security/advisories/GHSA-8rrh-rw8j-w5fx)
and [setuptools Unicode exclusion handling](https://setuptools.pypa.io/en/latest/history.html).
Pin setuptools 83.0.0 and wheel 0.46.2, rebuild and rerun the clean-runtime gates.

The Phase 1 foundation needs no Azure paid resource. Reusing the existing
workspace for read-only validation, using a small local Python package and
keeping deployment blocked is sufficient. New Azure technologies would add no
demonstrable value in this phase; model/agent improvements will be selected and
priced when their implementation phases begin.
