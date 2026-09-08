# Serving dependency review — 2026-09-08

GitHub reported 19 open MLflow advisories against the historical
`azure_databricks/environments/phase5_model_requirements.txt` snapshot (MLflow
3.8.1). That file is not the Phase 6 deployment environment and must not be used
to deploy the POC. It remains unchanged to preserve the original registration
record; no security alerts were dismissed or suppressed.

Candidate 3's actual model environment pins MLflow **3.16.0**. For the 17 alerts
with a patched-version entry, all reported patched versions were <=3.15.0.
The other two alerts had no patched-version entry but listed affected ranges
<=3.10.1 and <=3.8.1 respectively, so 3.16.0 is outside both reported ranges.

The two latter advisories concern a standalone MLflow tracking server using
`--app-name=basic-auth`: unauthenticated job execution routes and tracing/
assessment authorization. This POC deploys neither such a tracking server nor
allowlisted arbitrary job-execution functions. It uses managed Azure Databricks
identity authentication, a pinned custom PyFunc model, and scoped project groups.

This is a scoped review of the observed GitHub alerts, not a claim that every
transitive dependency or platform component has no vulnerabilities. Untrusted
pickles/models must never be loaded. Frozen model inputs and trained artifacts
remain the reviewed synthetic transfer; no user model uploads are accepted.

Source: GitHub Dependabot alerts 6–24 for the target repository, read on the date
above, and the registered model's explicit requirements. PR #12 targets the old
snapshot; it was not merged as part of this work.
