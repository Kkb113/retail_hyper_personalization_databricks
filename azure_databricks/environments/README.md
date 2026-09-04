# Runtime locks

These are **Phase 1 foundation locks**, not a claim that the frozen recommender
already runs in Databricks. Python 3.12 is the supported local foundation version.
No MLflow, Spark or model-loading dependency is required yet. Phase 5 must add
and test the actual composite-model runtime against the frozen artifacts before
logging or serving it. Installing a serving lock does not enable serving.

| Lock | Current purpose |
| --- | --- |
| `jobs.lock` | Configuration and Databricks SDK for future job tooling |
| `serving.lock` | Configuration-only foundation; inference is not implemented |
| `agent.lock` | Configuration and SDK; no LLM selection or calls |
| `app.lock` | Local FastAPI/uvicorn health skeleton; no cloud app deployment |

All direct and transitive versions/hashes are pinned. Regenerate deliberately
with the version of uv in `config/toolchain.json`, from the repository root:

```sh
uv pip compile pyproject.toml --extra jobs --generate-hashes --universal --python-version 3.12 -o azure_databricks/environments/jobs.lock --quiet
```

Repeat for `serving`, `agent`, `app`. The development lock combines `dev`, `jobs`
and `app` extras. Install with `pip install --require-hashes -r <lock>` and then
install the built project wheel using `pip install --no-deps <wheel>`.

Locks cover Linux/Windows through universal resolution, but platform support is
only verified where CI runs. The CI matrix performs a clean Linux install/import
for each runtime, while the main gate exercises the application and contracts.
Runtime versions in future Databricks jobs must be matched explicitly; do not
blindly overwrite libraries bundled with Databricks Runtime.
