# Runtime locks

These are **Phase 1 foundation locks**, not a claim that the frozen recommender
already runs in Databricks. Python 3.12 is the supported local foundation version.
The foundation locks intentionally exclude the scientific model runtime.
The registered version 2 uses `phase6_model_requirements.txt`, independently
validated by a real serving-container build. Installing a foundation serving
lock alone does not provide the model or network client dependencies.
`phase5_model_requirements.txt` is historical evidence: its MLflow 3.8.1 and
pandas 3.0.3 pins conflict under clean resolution. Do not deploy that file.

| Lock | Current purpose |
| --- | --- |
| `jobs.lock` | Configuration and Databricks SDK for future job tooling |
| `serving.lock` | Configuration-only foundation; registered inference has its own environment |
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
