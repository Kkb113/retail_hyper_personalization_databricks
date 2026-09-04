# Azure Databricks Retail Hyper-Personalization POC

## Phase-wise implementation plan

| Field | Value |
|---|---|
| Document status | Implementation source of truth |
| Initial delivery status | Phase 0 complete; Phase 1 foundation implemented and live-validated |
| Last verified | 2026-09-04 |
| Azure resource group boundary | **Databricks** only |
| Azure Databricks workspace | **intellify-databricks-demo** |
| Azure region | **West US** |
| Databricks catalog | **intellify_databricks_demo** |
| Cost profile | Free first; otherwise the smallest usage-based or scale-to-zero option |
| Data classification for this POC | Synthetic data only |

> This document supersedes the AWS Databricks Free Edition, local SQL Server, local Streamlit, and legacy standalone-agent deployment assumptions in the original source repository. Those implementations remain useful as references, but they are not part of this Azure repository or its target architecture.

---

## 1. Executive decision

The proposed POC is feasible in the current environment.

The existing Azure Databricks workspace is a Premium workspace, is successfully provisioned in West US, has Unity Catalog available, and exposes pay-per-token foundation-model endpoints. The local repository contains the prepared synthetic retail data, a frozen hybrid recommendation model, business-rule logic, cold-start handling, and testable recommendation services.

The solution should nevertheless be rebuilt as a clean Azure-native implementation rather than copying the existing Streamlit or Databricks prototype unchanged. In particular:

- The current local recommendation implementation is real and reusable.
- The current Databricks model-migration notebook is not a production inference package. It registers a component-status wrapper and a frozen recommendation lookup, rather than the complete adaptive recommender.
- The Azure build must package the actual retrieval, ranking, cold-start, routing, inventory, and business-rule behavior into a functional MLflow model.
- The LLM must explain, orchestrate, and converse. It must not invent products, prices, inventory, customer facts, or recommendation scores.
- Existing-customer recommendations should be precomputed in a Gold table for fast, inexpensive reads.
- New-customer and what-if recommendations should use a small scale-to-zero custom model-serving endpoint.
- One Databricks App should host both the user interface and the agent runtime so that a second always-on agent endpoint is unnecessary.

The end result will be a demonstrable, governed retail concierge with Customer 360, personalized recommendations, explainability, semantic product discovery, scenario simulation, feedback capture, operational notifications, and model/agent observability.

This is a high-quality POC architecture, not a claim of production readiness. Production approval will require real-data privacy review, representative holdout evaluation, business sign-off, load testing at expected scale, an operating model, and formal security approval.

---

## 2. Non-negotiable implementation rules

These rules apply to every future implementation phase.

### 2.1 Azure boundary

1. All Azure resource operations must be scoped to resource group **Databricks**.
2. The existing workspace **intellify-databricks-demo** is the only Databricks workspace in scope.
3. New Azure resources may be created only in **Databricks**, preferably in **West US** to avoid avoidable latency and cross-region transfer.
4. No resource may be created in another resource group as an implicit dependency.
5. No existing Azure or Databricks resource may be deleted, resized, or materially reconfigured without a read-only impact check and explicit confirmation.
6. Credentials, access tokens, connection strings, and secrets must never be committed to Git, notebooks, tables, logs, traces, or the resource inventory.

### 2.2 Cost boundary

The selection order is:

1. Existing capacity or a no-additional-resource implementation.
2. A genuine Free SKU, after confirming regional and subscription availability.
3. Usage-based serverless compute with auto-stop or scale-to-zero.
4. The smallest practical paid SKU, only when the prior options cannot satisfy the POC.

The following are disallowed by default:

- GPU serving.
- Provisioned LLM throughput.
- Always-on all-purpose clusters.
- Continuous pipelines or continuous vector-index synchronization.
- High-availability databases, replicas, or multi-region resources.
- An always-running Databricks App.
- Unbounded LLM output, tool loops, tracing payloads, or scheduled evaluations.
- Silent fallback from a Free SKU to a paid SKU.

Azure budgets notify but do not automatically stop consumption, so budget alerts must be paired with technical limits such as auto-stop, scale-to-zero, maximum worker counts, token ceilings, rate limits, paused schedules, and a demo start/stop runbook.[R15]

### 2.3 Quality boundary

- Every phase has a measurable exit gate.
- A phase is not complete merely because resources were created.
- Data and model migration require hashes, row counts, schema tests, and inference parity evidence.
- Agent answers containing product, price, inventory, promotion, customer, or metric facts must be grounded in tool output.
- Recommendation behavior must remain deterministic for the same model version, input, and business-rule snapshot.
- The POC must degrade safely if an LLM, serving endpoint, optional search service, or operational store is unavailable.

### 2.4 Change-management boundary

- Infrastructure and Databricks resources must be represented declaratively in Databricks Declarative Automation Bundles wherever supported.[R1][R2]
- Every created resource must be recorded in a non-secret resource inventory.
- Destructive cleanup remains a separately approved action even when a resource has a POC expiry tag.
- Schedules are deployed paused and enabled only after their tests pass.
- Optional paid features are behind configuration flags and default to off.

---

## 3. Verified starting point

### 3.1 Live Azure and Databricks state

The following was verified through read-only Azure CLI and Databricks API inspection on 2026-09-03:

| Item | Verified state | Implication |
|---|---|---|
| Azure CLI identity | Signed in; subscription state Enabled | Implementation can be automated after each mutation is reviewed |
| Resource group | Databricks | Hard scope boundary |
| Workspace | intellify-databricks-demo | Reuse; do not create another workspace |
| Region | West US | Model serving and Lakebase Autoscaling are documented as available |
| SKU | Premium | Unity Catalog, model serving, Databricks Apps, and governed AI capabilities are available |
| Provisioning | Succeeded | Base platform is healthy |
| Public network access | Enabled | POC access works; production network hardening is a later decision |
| Current identity | Active workspace user; member of admins and users | Sufficient for bootstrap, but least-privilege identities must be introduced |
| Project catalog | intellify_databricks_demo | Reuse this catalog |
| Project schemas | default and information_schema only | Application schemas still need to be created |
| Project volumes | None | Transfer volumes still need to be created |
| Registered project models | None | Functional recommender still needs registration |
| Unity Catalog functions | None | Agent tools still need implementation |
| Databricks Apps | None | App still needs implementation |
| Jobs | None | Data, scoring, evaluation, and maintenance jobs still need implementation |
| Visible foundation endpoints | 22 system endpoints | A live model bake-off can be run without deploying dedicated LLM capacity |

Visible low-cost model candidates include **databricks-gpt-oss-20b**, **databricks-meta-llama-3-1-8b-instruct**, and **databricks-claude-haiku-4-5**. Larger endpoints are also visible. Endpoint availability can change, so the final default must be selected through evaluation rather than hardcoded from this inventory.

### 3.2 Local repository state

The current repository is a strong source for migration inputs:

| Area | Current state |
|---|---|
| Retail data | Prepared synthetic datasets and feature snapshots |
| Model | Frozen adaptive hybrid recommender assets are present |
| Retrieval | ALS, content, metadata, co-purchase, promotion, and related candidate sources |
| Ranking | Known-customer and low-history rankers |
| Cold start | New-customer inference reference and policy |
| Routing | Adaptive customer-state routing |
| Business rules | Inventory, exclusions, diversity, promotion, and other deterministic policies |
| Existing interfaces | Local custom app, Streamlit, and earlier Databricks POC code |
| Release posture | POC/synthetic; formal release decision is still HOLD for production |

Focused local verification passed **25 tests in 9.53 seconds**, including migration-hash checks, asset auditing, existing-customer recommendation behavior, and cold-start policy.

The approved frozen loader verifies **26 model files**. Both the existing-customer and new-customer frozen models load successfully.

### 3.3 Transfer scope

The minimum functional transfer package contains:

- **45 unique files**
- **20 data files**
- **26 verified model files**
- **1 overlapping snapshot**, which explains why 20 plus 26 becomes 45
- **0 currently missing files**
- Approximately **12.14 MiB**
- Approximately **275,630 declared rows**

The broader local cache and artifact folders occupy approximately **63.34 MiB**. They should be archived only for reproducibility and audit; the runtime should depend on the smaller explicit transfer package.

The existing migration contract is incomplete. It omits several files needed by the approved v2 runtime, including:

- artifacts/retrieval_v2/als_factors.npz
- artifacts/retrieval_v2/content_matrix.npz
- artifacts/cold_start_v1/inference_reference.joblib
- artifacts/ranker_v2/known_ranker.json
- artifacts/ranker_v2/lowhistory_ranker.json

A new immutable transfer manifest must therefore be generated from the runtime dependency graph, not copied from the old 19-item migration contract.

### 3.4 Compatibility risk to resolve

The frozen artifacts loaded locally under newer packages than the repository pins:

| Package | Runtime used in local validation | Repository pin |
|---|---:|---:|
| numpy | 2.5.1 | 1.26.4 |
| pandas | 3.0.3 | 2.2.3 |
| pyarrow | 24.0.0 | 17.x |
| scikit-learn | 1.9.0 | 1.6.1 |
| xgboost | 3.3.0 | 2.1.4 |
| joblib | 1.5.3 | 1.5.1 |

Serialized Python and joblib artifacts are sensitive to dependency drift. Phase 5 must test a compatibility matrix on Databricks and record explicit MLflow dependencies. We must not assume that successful local deserialization guarantees portable model serving.

### 3.5 Baseline model metrics to preserve

The current repository records the following selected validation metrics:

| Metric | Current selected validation |
|---|---:|
| NDCG at 10 | 0.17685 |
| Recall at 10 | 0.24715 |
| Hit Rate at 10 | 0.44382 |
| Purchase Hit Rate at 10 | 0.12116 |
| Customer coverage | 1.00000 |
| Product coverage | 0.42280 |

These are migration baselines, not universal production targets. Azure parity tests must use the same frozen inputs and evaluation definitions before making any quality comparison.

---

## 4. POC outcomes and user journeys

### 4.1 Business outcomes

The POC must demonstrate that a retailer can:

- Retrieve a governed Customer 360 profile.
- Generate relevant, inventory-valid product recommendations.
- Handle known, low-history, and new customers.
- Explain why each product was recommended using factual evidence.
- Explore products using natural language and semantic similarity.
- Simulate safe, non-persistent what-if changes.
- Surface deterministic retail opportunities such as replenishment, price or promotion relevance, and cart recovery.
- Collect explicit user feedback.
- Observe model quality, agent quality, latency, usage, and approximate cost.

### 4.2 Primary journeys

#### Journey A: Known customer concierge

1. Select or authenticate as a demo user.
2. View Customer 360 signals.
3. Ask for recommendations within a stated need or budget.
4. Agent calls the customer, recommendation, inventory, promotion, and explanation tools.
5. The app displays ranked product cards with grounded reasons and evidence.
6. User provides thumbs-up, thumbs-down, or a reason.

#### Journey B: New customer onboarding

1. Enter non-sensitive preferences, budget, region, and optional context.
2. The cold-start route generates recommendations.
3. The app states that the result is preference-based rather than purchase-history-based.
4. Feedback is captured for future evaluation.

#### Journey C: What-if digital twin

1. Start from a real synthetic profile.
2. Change one or more attributes in an isolated scenario.
3. Compare the original and scenario recommendation lists.
4. Display added, removed, and reranked products with causal evidence.
5. Do not persist the simulated profile to the governed customer record.

#### Journey D: Natural-language product discovery

1. Ask for a concept such as a use case, style, dietary need, gift intent, or budget.
2. Semantic retrieval finds candidates.
3. Structured filters enforce inventory, category, region, price, and policy.
4. The agent returns real product identifiers only.

#### Journey E: Analyst and executive view

1. Open the AI/BI dashboard.
2. Inspect recommendation coverage, source mix, cold-start share, feedback, latency, and bounded cost.
3. Optionally use a curated Genie Space for human natural-language cohort questions.

---

## 5. Target architecture

~~~mermaid
flowchart LR
    U[Demo user or analyst] --> APP[Databricks App<br/>React UI plus AgentServer API]

    APP --> AG[ResponsesAgent<br/>bounded tool loop]
    AG --> FM[Pay-per-token foundation model<br/>selected by bake-off]
    AG --> T[Governed deterministic tools]

    T --> SQL[Small serverless SQL warehouse<br/>one-minute API auto-stop]
    T --> BATCH[Gold recommendation tables<br/>known-customer fast path]
    T --> EP[Scale-to-zero MLflow endpoint<br/>new-customer and what-if path]
    T --> OPS[Lakebase Autoscaling<br/>feedback, sessions, notifications]

    SQL --> UC[Unity Catalog Delta tables]
    BATCH --> UC
    EP --> M[Functional composite recommender<br/>MLflow plus Unity Catalog]
    M --> V[Versioned model assets<br/>Unity Catalog volume]

    UC --> EMB[Precomputed product embeddings<br/>bounded cosine search]
    EMB --> T

    J[Triggered serverless jobs] --> UC
    J --> M
    J --> BATCH
    J --> EVAL[MLflow evaluation and traces]

    APP --> TRACE[MLflow traces]
    AG --> TRACE
    EVAL --> DASH[AI/BI quality and cost dashboard]
    UC --> DASH
    OPS --> DASH
~~~

### 5.1 Why this architecture

| Decision | Reason |
|---|---|
| Reuse the existing Premium workspace and catalog | Avoids new core Azure infrastructure |
| Managed Unity Catalog tables and volumes | Lowest operational complexity, governed lineage, privileges, and discoverability[R3][R4][R5] |
| Triggered serverless jobs | No idle job cluster and simpler dependency management[R6] |
| Smallest serverless SQL warehouse | Fast app queries with aggressive auto-stop; warehouses continue to incur usage while idle, so auto-stop is mandatory[R7][R8] |
| Batch Gold recommendations for known customers | Lower latency and serving cost for the dominant demo flow |
| One scale-to-zero custom model endpoint | Handles cold start and dynamic scenarios without permanent compute; accepts cold-start latency for POC[R9][R10] |
| One Databricks App for UI and agent | Native auth/resource bindings and fewer separately deployed services[R11][R12][R13] |
| AgentServer inside the app | A standard ResponsesAgent contract and MLflow tracing without another agent-serving endpoint[R16][R17][R18] |
| Pay-per-token foundation model | Suits experimentation and avoids provisioned capacity[R19] |
| Lakebase Autoscaling with a Delta fallback | Low-latency operational state with rapid scale-to-zero; not a core dependency if quota or cost fails[R20][R21] |
| Embeddings in Delta plus bounded in-process search | Delivers semantic discovery for the small catalog without a continuously billed vector endpoint |
| AI/BI dashboard | Native, shareable decision support with minimal extra platform surface[R22] |

---

## 6. Clean Azure project layout

The Azure implementation should live in a new subtree. Legacy implementations remain untouched until the POC is accepted.

~~~text
azure_databricks/
  README.md
  databricks.yml
  resources/
    schemas.yml
    volumes.yml
    jobs.yml
    warehouse.yml
    model_serving.yml
    app.yml
    lakebase.yml
    dashboards.yml
  src/
    retail_hp_azure/
      config/
      data/
      features/
      recommendation/
      serving/
      agent/
      tools/
      observability/
      security/
  notebooks/
    bootstrap/
    migration/
    features/
    scoring/
    evaluation/
  app/
    app.yaml
    backend/
    frontend/
  contracts/
    transfer_manifest.json
    data_contracts/
    model_contract.json
    tool_contracts/
    evaluation_sets/
  scripts/
    preflight.ps1
    transfer.ps1
    validate_bundle.ps1
    demo_start.ps1
    demo_stop.ps1
    inventory.ps1
  tests/
    unit/
    contract/
    integration/
    parity/
    agent/
    app/
  evidence/
    phase_00/
    phase_01/
    ...
~~~

Key repository-level artifacts will include:

- **azure_databricks/databricks.yml**: one authoritative POC bundle.
- **azure_databricks/contracts/transfer_manifest.json**: every data/model file, hash, size, role, and destination.
- **azure_databricks/resource_inventory.json**: every live resource, identifier, SKU, owner tag, cost class, and lifecycle state; never secrets.
- **azure_databricks/cost_ledger.md**: estimated cost decision, actual observed usage, and shutdown state per phase.
- **azure_databricks/evidence/**: command output, test reports, evaluation summaries, screenshots, and phase decisions.

The root-level legacy **databricks.yml** must not be reused unchanged because its old targets and assumptions do not represent the Azure workspace.

---

## 7. Unity Catalog namespace

Reuse catalog **intellify_databricks_demo** and create the following managed schemas:

| Schema | Purpose |
|---|---|
| bronze | Immutable landed data represented as Delta plus ingestion metadata |
| silver | Conformed customer, product, transaction, inventory, promotion, and interaction entities |
| features | Offline model features and point-in-time feature snapshots |
| ml | Registered recommender and evaluation artifacts |
| gold | Customer 360, current/history recommendations, explanations, opportunities, and app-ready aggregates |
| serving | Agent-facing governed views and SQL functions |
| agent | Prompt versions, tool metadata, evaluation sets, and non-secret agent configuration |
| monitoring | Data quality, model quality, agent quality, latency, usage, and approximate cost |

Create two managed volumes:

| Volume | Purpose |
|---|---|
| bronze.transfer_landing | Immutable versioned source packages and manifests |
| ml.model_assets | Versioned frozen assets and MLflow packaging inputs |

Suggested fully qualified objects:

- **intellify_databricks_demo.ml.adaptive_recommender**
- **intellify_databricks_demo.gold.customer_recommendation_current**
- **intellify_databricks_demo.gold.customer_recommendation_history**
- **intellify_databricks_demo.gold.customer_360**
- **intellify_databricks_demo.gold.recommendation_opportunity**
- **intellify_databricks_demo.agent.feedback**
- **intellify_databricks_demo.monitoring.agent_quality**

Managed storage is the default. External storage, a new storage account, or an external location should be introduced only if a client data-integration requirement later makes it necessary.

---

## 8. Component and cost decision matrix

| Capability | POC selection | Cost control | Alternative or fallback |
|---|---|---|---|
| Data governance | Unity Catalog managed Delta and volumes | Reuse existing catalog | None required |
| Orchestration | Triggered serverless Lakeflow Jobs | No always-on cluster; schedules paused by default | Small single-node job compute only if serverless is unavailable |
| App query engine | Smallest serverless SQL warehouse | Min/max cluster 1; API auto-stop 1 minute | Direct Delta/Spark job only for offline flows |
| Recommender registry | MLflow 3 plus Unity Catalog | Reuse platform capabilities | None |
| Known-user serving | Gold Delta table | Batch recompute only on demand or bounded schedule | Model endpoint |
| Dynamic model serving | Small CPU endpoint with scale-to-zero | One served model; no GPU; warm only for demo | Execute inside a job for non-interactive testing |
| LLM | Existing pay-per-token foundation endpoint | Model bake-off; token/tool ceilings; AI Gateway rate limit | Smaller endpoint or deterministic non-LLM response |
| Agent runtime | MLflow ResponsesAgent plus AgentServer in Databricks App | No separate agent endpoint | Lightweight FastAPI tool orchestrator |
| UI | Databricks App, MEDIUM compute | Stop outside active development and demo windows | SQL dashboard-only fallback |
| Operational state | One Lakebase Autoscaling project | Minimum 0.5 CU, maximum 1 CU, five-minute scale-to-zero target, no HA/replica | Delta state tables |
| Semantic retrieval | One-time embeddings in Delta plus bounded cosine search | No continuously billed vector endpoint | Azure AI Search Free, only if truly available |
| Observability | MLflow traces plus Delta metrics | Sampling, retention, and payload redaction | Application logs plus evaluation tables |
| Analytics | AI/BI dashboard | Queries share the aggressively auto-stopped warehouse | Static evaluation notebook |
| Natural-language BI | Optional curated Genie Space | Human demo only; no service-principal automation by default | Dashboard filters and trusted SQL |
| Search service | Optional Azure AI Search Free | Create only after Free quota preflight; never auto-upgrade to Basic | Default Delta embedding search |
| Data-quality monitoring | Explicit validation job | Run at ingestion and release gates, not continuously | One bounded managed-monitor demonstration |

### 8.1 Technologies intentionally not selected by default

#### Databricks AI Search

Databricks AI Search is well integrated with Unity Catalog, but its endpoint has a base vector-unit cost after an index exists. Continuous synchronization also costs more than triggered synchronization.[R23][R24] The catalog in this POC is small enough to precompute embeddings and search them in memory or through bounded SQL/Python. AI Search can be reconsidered only if scale or latency measurements justify it.

#### Azure AI Search paid tiers

Azure AI Search has a dedicated Free tier with strict limits and one service per subscription, subject to availability.[R25][R26] It is an optional enhancement, not a dependency. If the Free service is already consumed, unavailable, or cannot meet the required security configuration, creation stops; the implementation must not silently select Basic.

#### Online Feature Store

The default online-store path adds unnecessary complexity and its documented deployment profile does not provide the same scale-to-zero behavior desired here.[R27] Offline Unity Catalog feature tables plus batch recommendations and one dynamic model endpoint are sufficient for this POC.

#### New Azure OpenAI, Key Vault, or storage resources

The workspace already exposes governed foundation endpoints and managed storage. Additional Azure resources would increase cost and operational surface without proving a unique requirement. Databricks secrets and resource bindings are sufficient for the initial POC; a client-standard Key Vault-backed secret scope can be added later if mandated.

---

## 9. Delivery roadmap

| Phase | Name | Primary outcome | Initial status |
|---:|---|---|---|
| 0 | Scope, safety, and cost baseline | Frozen scope, live inventory, cost guardrails, and decision log | Complete |
| 1 | Azure project foundation | Clean bundle-based repository, environments, tests, and CI-ready structure | Not started |
| 2 | Governance and platform bootstrap | Schemas, volumes, identities, permissions, warehouse, tags, and budget controls | Not started |
| 3 | Immutable data and model transfer | Complete, hash-verified transfer package in Unity Catalog volumes | Not started |
| 4 | Lakehouse and feature foundation | Bronze, Silver, Feature, Gold, lineage, and data-quality gates | Not started |
| 5 | Functional MLflow recommender | Real composite inference package with Azure parity evidence | Not started |
| 6 | Batch and real-time recommendation serving | Fast known-user path plus dynamic cold-start/what-if path | Not started |
| 7 | Operational state and feedback | Low-latency sessions, feedback, notifications, and idempotency | Not started |
| 8 | Semantic intelligence and governed tools | Product semantics and deterministic, permission-aware tool layer | Not started |
| 9 | Agent implementation and evaluation | Grounded retail concierge with model bake-off and safeguards | Not started |
| 10 | Databricks App and wow experiences | Polished end-to-end user experience | Not started |
| 11 | Analytics, observability, and operations | Quality, usage, cost, trace, and business dashboards | Not started |
| 12 | Release qualification and demo freeze | Reproducible, tested, documented POC with rollback and shutdown | Not started |

---

## 10. Detailed phase plan

## Phase 0 — Scope, safety, and cost baseline

### Objective

Turn the current assessment into a controlled implementation contract before any cloud mutation.

### Build activities

1. Capture Azure subscription, resource-group, workspace, catalog, model-endpoint, identity, quota, and regional-capability inventory.
2. Record the resource group ID and workspace ID as immutable deployment guardrails.
3. Generate a proposed-resource plan with resource names, types, SKU or compute size, cost class, auto-stop behavior, and deletion policy.
4. Define tags:
   - project = retail-hyper-personalization
   - environment = poc
   - managed-by = codex
   - cost-profile = low
   - auto-stop = true where applicable
   - workspace = intellify-databricks-demo
5. Create a cost ledger and obtain a numeric monthly alert threshold before creating an Azure Cost Management budget. Do not invent a financial approval limit.
6. Freeze the POC scope and non-goals.
7. Generate the initial 45-file transfer manifest from actual runtime dependencies.
8. Preserve a read-only snapshot of the current local test and model metric results.

### Deliverables

- phase_00/live_inventory.json
- phase_00/proposed_resources.md
- phase_00/local_baseline.json
- contracts/transfer_manifest.json
- resource_inventory.json
- cost_ledger.md
- architecture decision records

### Tests

- Every proposed Azure resource resolves to resource group **Databricks**.
- Every Databricks resource resolves to workspace **intellify-databricks-demo**.
- Every paid resource has a shutdown, auto-stop, scale-to-zero, or explicit bounded-use policy.
- No secret exists in generated inventory files.
- All local runtime dependencies appear in the transfer manifest.

### Cost controls

- Read-only phase except for an approved budget or diagnostic artifact.
- Record contract pricing or pricing-calculator evidence immediately before each paid creation because published prices and negotiated rates can change.
- Define a client-approved alert threshold; remember that the alert does not hard-stop spend.

### Exit gate

**GO** only when the scope boundary, proposed resource list, transfer manifest, technical caps, and numeric budget alert decision are recorded.

---

## Phase 1 — Azure project foundation

**Implemented:** Azure-only Python package, one `poc` bundle with no resources,
strict scope/cost preflight, four hash-locked foundation runtimes, local app
health skeleton, schema, tests and read-only live validation. See
`azure_databricks/evidence/phase_01/` for the review, research and evidence.
No cloud deployment, compute creation or artifact upload occurs in this phase.
The runtime locks are foundation-only; functional model dependencies and parity
remain Phase 5 work. The bundle uses a private runtime-resolved user root and
only a harmless marker is eligible for future synchronization.

**Current owner decisions override earlier planning assumptions:** INR 12,000
monthly target, INR 9,000 internal stop target, INR 3,000 reserve, at most four
occasional demos per month, and a 20-minute human-idle objective. Both alert
recipients are supplied privately. Azure budgets are not hard billing caps.
Budget delivery and shutdown controllers are not deployed; all optional features
and cloud mutations remain disabled in `azure_databricks/config/poc.json`.
Custom serving stays disabled while its native 30-minute idle scale-down cannot
meet that objective. No Premium add-ons or free-to-paid fallback is authorized.

### Objective

Create a clean, reproducible Azure Databricks codebase without importing obsolete deployment assumptions.

### Build activities

1. Create the **azure_databricks/** subtree described in Section 6.
2. Define a single bundle target named **poc** for the existing Azure workspace.
3. Parameterize catalog, schemas, warehouse, endpoint, app, and optional-feature flags.
4. Add preflight validation that fails when:
   - The active Azure resource group is not Databricks.
   - The workspace host does not match intellify-databricks-demo.
   - A proposed Azure resource lacks required cost tags.
   - A paid optional feature is enabled without an approval flag.
5. Add linting, type checking, unit tests, contract tests, and bundle validation commands.
6. Create environment-lock files for job, serving, agent, and app runtimes.
7. Add a secrets template containing names only, never values.
8. Add evidence-generation helpers so every deployment produces a machine-readable plan and result.
9. Keep the legacy code readable, but introduce no runtime imports from the Streamlit UI, local SQL Server layer, AWS-specific notebooks, or legacy agent.

### Deliverables

- Validated bundle configuration.
- Installable Python package.
- App skeleton.
- Automated test structure.
- Preflight and inventory scripts.
- POC configuration schema.

### Tests

- Bundle validation passes against the live workspace.
- A dry-run names only in-scope resources.
- Package import tests pass in a clean environment.
- A secret scanner finds no credentials.
- Legacy Azure/AWS host strings cannot enter the new POC target.

### Cost controls

- Bundle validation and local tests precede deployment.
- No compute resource is created in this phase unless required for a separately approved validation.

### Exit gate

The bundle is reproducible, validates against the live workspace, and cannot target another resource group or workspace accidentally.

---

## Phase 2 — Governance and platform bootstrap

### Objective

Create the minimum governed Databricks foundation for data, models, jobs, the app, and observability.

### Build activities

1. Create the managed schemas and volumes listed in Section 7.
2. Define least-privilege groups:
   - retail_hp_admins
   - retail_hp_engineers
   - retail_hp_viewers
3. Retain the current admin identity for bootstrap only; prepare a workload identity for automated deployments when the client identity model is confirmed.
4. Provision the smallest serverless SQL warehouse:
   - Min clusters: 1
   - Max clusters: 1
   - Auto-stop through API: 1 minute
   - No prewarming outside a demo
5. Define grants at catalog, schema, table, volume, model, warehouse, and app-resource levels.
6. Configure audit-friendly naming and required tags.
7. Create a resource-group budget after the numeric alert amount is approved.
8. Add a POC expiry field to the inventory, but do not automate deletion.
9. Validate that Databricks system billing tables are visible for later usage reporting.

### Deliverables

- Governed namespaces.
- Managed volumes.
- Least-privilege matrix.
- Small serverless warehouse.
- Budget alert configuration or a documented pending approval.
- Updated resource and cost inventories.

### Tests

- Viewer can query approved serving views but cannot modify source tables or models.
- App identity cannot browse raw transfer files.
- Engineers can run jobs without becoming account administrators.
- Warehouse stops after the configured idle period.
- No resource exists outside the approved boundary.

### Cost controls

- One SQL warehouse only.
- Serverless and auto-stop.
- No all-purpose cluster.
- No new Azure storage account.
- No running schedule.

### Exit gate

Namespace, access, warehouse, budget controls, and cost tags pass automated inspection.

---

## Phase 3 — Immutable data and model transfer

### Objective

Transfer the prepared synthetic data and all functional model dependencies to a versioned, hash-verifiable Unity Catalog landing area.

### Build activities

1. Build a versioned package such as **retail_hp_transfer_v1**.
2. Include the 20 required data files and all 26 approved runtime model files.
3. For every file record:
   - Logical role
   - Local relative path
   - Destination URI
   - Byte size
   - SHA-256
   - Declared row count where applicable
   - Schema/version identifier
   - Required-by component
4. Upload to an immutable version directory under **bronze.transfer_landing**.
5. Upload model binaries to the versioned **ml.model_assets** location or reference the immutable landed copy during packaging.
6. Recompute hashes inside Databricks.
7. Parse every structured data asset and validate schema, row count, primary-key expectations, and null constraints.
8. Execute frozen-model load tests in a Databricks job environment.
9. Archive the broader 63.34 MiB repository artifacts only if they are needed for audit; do not make the runtime depend on the archive.
10. Mark the manifest immutable after validation.

### Deliverables

- Complete transfer package.
- Local and remote hash reports.
- Data-asset audit report.
- Model-load report.
- Immutable manifest and version ID.

### Tests

- 45 of 45 unique runtime files are present.
- 26 of 26 model files match approved hashes.
- All 20 data assets parse successfully.
- Declared and observed row counts agree.
- No unexpected executable, credential, or PII file is present.
- Existing-customer and new-customer frozen model loaders initialize on Databricks.

### Cost controls

- The package is small; use volume upload rather than introducing a storage service.
- Use one triggered serverless validation job.
- Do not duplicate large intermediate data across arbitrary workspace paths.

### Exit gate

The remote package is complete, immutable, hash-identical, parseable, and loadable. A missing or incompatible model asset is a hard stop.

---

## Phase 4 — Lakehouse and feature foundation

### Objective

Create governed, reusable Delta tables that reproduce the current feature and recommendation inputs with lineage and leakage-safe semantics.

### Build activities

1. Ingest each landed asset into append-only Bronze tables with:
   - transfer_version
   - source_file
   - source_hash
   - ingested_at
   - run_id
2. Build conformed Silver entities for:
   - customers
   - products
   - transactions
   - transaction_items
   - browsing and interaction events
   - inventory
   - promotions
   - product metadata
   - customer preferences
3. Implement deterministic identity, type, currency, timestamp, category, and region conformance.
4. Recreate customer and product features in Unity Catalog feature tables.[R28]
5. Use event-time keys and point-in-time joins for time-dependent features to prevent future-data leakage.[R29]
6. Build Gold tables and views:
   - customer_360
   - product_360
   - current_inventory
   - active_promotions
   - eligible_product_catalog
7. Encode data-quality expectations as executable tests:
   - Primary-key uniqueness
   - Referential integrity
   - Valid timestamps and price ranges
   - Inventory non-negativity
   - Promotion date consistency
   - Category and region domain checks
   - Feature freshness
8. Publish data lineage and a compact data dictionary.
9. Implement idempotent incremental MERGE patterns even though the initial transfer is a full snapshot.

### Deliverables

- Bronze, Silver, Feature, and initial Gold tables.
- Data contracts and dictionary.
- Feature lineage.
- Ingestion and feature jobs.
- Data-quality report.

### Tests

- Source-to-Bronze row reconciliation is 100 percent.
- Required primary keys are unique and non-null.
- All foreign-key orphan rates are zero or explicitly waived.
- Feature results match local golden samples within defined numeric tolerance.
- Re-running the same transfer version changes no business rows.
- Point-in-time tests prove that future interactions cannot enter historical features.

### Cost controls

- Triggered jobs only.
- Delta optimization only when table size and query evidence justify it.
- Avoid managed continuous data-quality monitoring in the default POC because it adds serverless usage; run explicit validations at ingestion and release gates.[R30]

### Exit gate

All governed tables reconcile to the frozen input, feature parity passes, and data-quality exceptions are zero or documented and approved.

---

## Phase 5 — Functional MLflow recommender

### Objective

Package the actual adaptive recommender—not a placeholder or snapshot lookup—as a portable MLflow model registered in Unity Catalog.

### Functional boundary

The composite model must include:

- ALS candidate retrieval.
- Content and metadata retrieval.
- Co-purchase and promotion retrieval.
- Known-customer and low-history rankers.
- Cold-start inference.
- Adaptive route selection.
- Candidate merging and deduplication.
- Inventory and eligibility filtering.
- Diversity and business rules.
- Explanatory evidence fields.
- Deterministic fallback behavior.

The model accepts typed records and returns structured recommendation rows. It must not call an LLM.

### Build activities

1. Extract the reusable recommendation domain logic into the new package without importing UI or database concerns.
2. Define an input contract supporting:
   - existing_customer
   - new_customer
   - scenario
3. Define output columns:
   - request_id
   - customer_id or scenario_id
   - rank
   - product_id
   - score
   - route
   - candidate_sources
   - reason_codes
   - inventory_snapshot_at
   - model_version
   - policy_version
4. Implement an MLflow PythonModel or equivalent PyFunc with explicit model signature, input example, artifacts, and pinned dependencies.[R9]
5. Run the dependency compatibility matrix:
   - Repository-pinned environment.
   - Current locally validated environment.
   - Selected Databricks serving-compatible environment.
6. Prefer a tested compatible pin set over automatic upgrades.
7. Log the model to MLflow, register it as **intellify_databricks_demo.ml.adaptive_recommender**, and initially assign alias **Candidate**.
8. Execute local-versus-Databricks golden-set parity.
9. Compare the Azure implementation against baseline ranking metrics.
10. Assign alias **Champion** only after every parity and policy gate passes.
11. Record model, transfer, feature, code, and policy versions together.

### Deliverables

- Functional composite MLflow model.
- Model signature and input examples.
- Environment lock and dependency report.
- Golden-set parity report.
- Registered Candidate version.
- Champion promotion decision.

### Tests

- Model loads in a clean job environment.
- Model loads in the selected serving image.
- Route selection matches the local implementation for 100 percent of golden cases.
- Top-10 product ordering matches exactly for deterministic cases.
- Floating scores remain within a recorded tolerance.
- Inventory validity is 100 percent.
- Customer coverage is 100 percent for eligible test customers.
- New-customer fallback never returns an unknown product.
- Invalid or incomplete input returns a typed validation error.
- Model serialization contains no secrets or absolute local paths.

### Cost controls

- Reuse frozen assets; no full retraining in the first Azure pass.
- Run compatibility experiments as short triggered jobs.
- Register only meaningful candidate versions.
- Keep model artifacts compact and avoid duplicate copies.

### Exit gate

The registered Candidate is a real end-to-end recommender, passes golden parity and inventory rules, and has a reproducible environment. Only then may it become Champion.

---

## Phase 6 — Batch and real-time recommendation serving

### Objective

Serve recommendations through the least expensive path appropriate to each customer state.

### Path A: Existing and low-history customers

1. Run a triggered batch scoring job using the Champion model.
2. Write versioned results to **gold.customer_recommendation_history**.
3. Atomically update **gold.customer_recommendation_current**.
4. Publish an app-facing view containing only eligible, current products and compact evidence.
5. Retain enough history for comparison without duplicating model binaries.

### Path B: New-customer and scenario requests

1. Create one custom model-serving endpoint such as **retail-hp-poc-recommender**.
2. Use the smallest CPU workload that passes latency tests.
3. Enable scale-to-zero.
4. Serve one Champion model version at a time during the POC.
5. Add request validation, timeout, retry, idempotency, and structured errors.
6. Add a demo warm-up action because scale-to-zero can introduce a cold start. Databricks documents typical custom-model cold starts around tens of seconds, but they can be longer and have no SLA.[R10]

### Business-rule enforcement

Rules are applied deterministically:

- Remove out-of-stock or ineligible items.
- Apply region, age, dietary, category, and policy constraints where represented in the synthetic data.
- Enforce excluded-product and already-purchased rules where configured.
- Maintain diversity and maximum-per-category limits.
- Validate promotion dates and displayed price.
- Return fewer than K products rather than fabricate a result.

### Deliverables

- Batch scoring job.
- Current and history recommendation tables.
- Scale-to-zero model endpoint.
- Typed recommendation client.
- Serving SLO and cold-start runbook.

### Tests

- Batch and endpoint results agree for identical supported inputs.
- Atomic promotion prevents partial current tables.
- Endpoint health, invalid-input, timeout, and retry tests pass.
- A scaled-down endpoint successfully wakes and returns a valid response.
- No returned product violates inventory or eligibility.
- Repeated idempotent requests do not create duplicate feedback or audit events.

### Cost controls

- Known-customer traffic uses Delta rather than endpoint compute.
- One small CPU endpoint only.
- Scale-to-zero enabled.
- No GPU, provisioned concurrency, traffic shadow, or duplicate serving endpoint.
- Warm the endpoint immediately before a demo and allow it to scale down afterward.

### Exit gate

Both paths return schema-valid, governed recommendations; the batch path is fast, and the dynamic path meets the agreed warm-latency target.

---

## Phase 7 — Operational state and feedback

### Objective

Provide low-latency mutable state without using the analytical lakehouse as an OLTP database.

### Preferred implementation

Create one Lakebase Autoscaling project only after cost and quota preflight. Lakebase is designed for transactional application use cases and supports scale-to-zero on autoscaling projects.[R20][R21]

Target POC configuration:

- One project and one database.
- Minimum compute: 0.5 CU if the live API permits it.
- Maximum compute: 1 CU.
- Scale-to-zero target: five minutes.
- No high availability.
- No read replica.
- No public credential committed anywhere.
- Connection exposed to the app through a Databricks App resource binding or secret reference.

### Operational tables

- conversation
- message
- feedback
- recommendation_impression
- scenario_session
- notification
- idempotency_key
- app_audit_event

Optional cache tables may hold a compact, refreshable subset of recommendations or product cards. Unity Catalog remains the system of record for analytical and model data.

### Fallback implementation

If Lakebase quota, regional availability, authentication, or observed cost does not satisfy the guardrail, use append-only Delta tables for feedback and session checkpoints. The app can keep ephemeral conversation state in memory for a single demo session. No core recommendation capability may depend exclusively on Lakebase.

### Deliverables

- Operational schema and migrations.
- Lakebase project or documented Delta fallback.
- App connection contract.
- Retention policy.
- Feedback-to-Delta export job.

### Tests

- Connection uses the app identity and least privilege.
- Scale-to-zero and wake behavior are observed.
- Feedback writes are idempotent.
- Conversation records cannot cross demo-user boundaries.
- Operational changes are exported to governed Delta tables.
- The app continues in read-only mode when the operational store is unavailable.

### Cost controls

- One project, capped at 1 CU.
- Rapid scale-to-zero.
- No HA or replica.
- Short retention for transient messages.
- Triggered export rather than continuous synchronization unless the demo requires it.

### Exit gate

Feedback and session state work with least privilege, bounded compute, and a tested failure fallback.

---

## Phase 8 — Semantic intelligence and governed tool layer

### Objective

Give the agent useful retail intelligence while ensuring that every action is deterministic, typed, observable, and permission-aware.

### Semantic product discovery

Default design:

1. Build a concise product search document from approved product attributes.
2. Generate embeddings once using a currently available embedding endpoint such as **databricks-gte-large-en**, subject to live validation.
3. Store the embedding as an array column in a governed Delta table with embedding-model version and generated timestamp.
4. Load the small eligible catalog into a bounded app-side or tool-side search structure.
5. Apply cosine similarity followed by structured inventory, price, category, promotion, and region filters.
6. Regenerate embeddings only when product text or the embedding model changes.

This design provides semantic search without an always-billed vector endpoint. If the catalog later outgrows bounded search, reassess Databricks AI Search using a triggered-sync endpoint.

### Governed tool catalog

| Tool | Purpose | Read/write | Source of truth |
|---|---|---|---|
| get_customer_360 | Retrieve authorized profile, segments, preferences, and summary metrics | Read | serving view |
| get_recommendations | Retrieve batch results or invoke dynamic serving | Read | Gold table or MLflow endpoint |
| explain_recommendation | Return structured reason codes and factual evidence | Read | recommendation evidence |
| search_products | Semantic plus structured product discovery | Read | product embedding and catalog tables |
| get_product_details | Return current product, price, inventory, and promotion facts | Read | serving views |
| compare_products | Produce a factual comparison matrix | Read | serving views |
| simulate_scenario | Run a non-persistent what-if recommendation | Read-like | MLflow endpoint |
| get_opportunities | Retrieve deterministic retail opportunities | Read | Gold opportunity table |
| record_feedback | Save explicit feedback | Write | Lakebase or Delta |
| get_quality_summary | Return approved aggregate model and agent metrics | Read | monitoring tables |

### Tool contract rules

- JSON-schema input and output.
- Stable tool and schema version.
- Maximum row and text limits.
- Strict customer authorization before lookup.
- Explicit timeouts and bounded retries.
- No arbitrary SQL supplied by the LLM.
- No free-form table or column identifiers.
- Writes require explicit user intent and idempotency.
- Every result includes source timestamp and data/model version.
- Every call emits a trace event with redacted arguments.

### Optional Azure AI Search Free enhancement

Use only if:

- The subscription does not already consume its Free-service allowance.
- West US and the required features are available.
- The resource can be created in resource group Databricks.
- The deployment plan explicitly specifies the Free SKU.
- A failed Free creation terminates the attempt rather than selecting Basic.

The core demo must already work without it.

### Deliverables

- Versioned embedding table and generation job.
- Deterministic product search.
- Typed tool package.
- Unity Catalog views/functions where appropriate.
- Authorization and tool-audit tests.

### Tests

- Semantic queries return relevant known products from the catalog.
- All returned identifiers exist and are eligible.
- Structured filters override semantic similarity.
- Unauthorized customer lookup returns no data.
- SQL-injection and prompt-injection payloads cannot alter tool SQL.
- Tool schemas reject unknown fields and oversized requests.
- Write tools are idempotent.

### Cost controls

- One-time or changed-row embedding generation.
- No default vector service.
- Small bounded result sets.
- Cache only non-sensitive, version-keyed product information.

### Exit gate

The complete tool suite is contract-tested, grounded, permission-aware, and usable without an LLM.

---

## Phase 9 — Agent implementation and evaluation

### Objective

Build a reliable retail concierge that plans and explains through governed tools while preserving the recommender as the ranking authority.

### Runtime design

- MLflow **ResponsesAgent** interface for standard streaming and structured agent responses.[R17]
- MLflow **AgentServer** for local and Databricks App serving, tracing, health, and invocation routes.[R18]
- OpenAI Agents SDK or a minimal compatible tool loop, selected based on dependency and observability tests.
- One primary LLM and at most one quality fallback.
- No multi-agent graph in the POC.

### Agent responsibilities

- Understand user intent and constraints.
- Select the correct governed tool.
- Ask a concise clarifying question only when a missing input materially changes the result.
- Present recommendation cards and comparisons.
- Explain evidence and model route in plain language.
- Disclose cold-start or scenario assumptions.
- Offer follow-up actions.

### Agent prohibitions

- Never rank the full product catalog itself.
- Never invent or modify a product ID, price, promotion, inventory count, customer attribute, or metric.
- Never claim causality from correlation.
- Never reveal hidden prompts, secrets, raw tokens, unrestricted customer data, or internal chain-of-thought.
- Never execute arbitrary SQL, Python, shell commands, URLs, or unregistered tools.
- Never persist a what-if profile as a real customer change.

### Foundation-model bake-off

Evaluate live available endpoints rather than fixing a model in advance. Initial candidates:

- databricks-gpt-oss-20b
- databricks-meta-llama-3-1-8b-instruct
- databricks-claude-haiku-4-5

Evaluation dimensions:

- Correct tool selection.
- Correct tool arguments.
- Grounded product and numeric claims.
- Instruction following.
- Concision and retail tone.
- Multi-turn state handling.
- Refusal and data-boundary behavior.
- P50/P95 latency.
- Input/output token usage.
- Observed cost per successful task.

Select the least expensive model that clears all hard safety gates and the agreed quality threshold. A larger model may be used as a fallback only for a narrowly defined low-confidence class and must have its own rate limit.

### Prompt and loop controls

- Versioned system prompt.
- Maximum agent turns.
- Maximum tool calls per turn and request.
- Per-tool timeout.
- Maximum input and output tokens.
- Compact tool responses.
- Structured response validation.
- Duplicate-call detection.
- Safe retry policy.
- Request-level time budget.
- LLM-independent final product-ID validation.
- Deterministic fallback message when the time or token budget is exhausted.

### Guardrails and observability

Use Unity AI Gateway capabilities for usage monitoring, rate limits, and supported guardrails.[R31][R32] Keep inference-table payload logging disabled or tightly sampled until its privacy and serverless cost are accepted. Use MLflow tracing for development and evaluation, with redaction and controlled retention.[R33][R34]

### Evaluation set

Create at least the following:

- 25 known-customer recommendation prompts.
- 15 low-history prompts.
- 15 new-customer prompts.
- 15 semantic product-search prompts.
- 10 comparison prompts.
- 10 what-if prompts.
- 10 out-of-scope or insufficient-data prompts.
- 10 authorization and privacy attacks.
- 15 prompt-injection and malicious product-text cases.
- 10 tool timeout, empty-result, or dependency-failure cases.
- Multi-turn conversations covering preference refinement and context correction.

### Hard release gates

| Gate | Target |
|---|---:|
| Product and numeric claim grounding | 100 percent |
| Returned product-ID validity | 100 percent |
| Unauthorized customer-data disclosures | 0 |
| Write without explicit intent | 0 |
| Tool argument schema validity | At least 98 percent |
| Correct tool selection | At least 95 percent |
| Successful safe handling of injection/security set | 100 percent |
| Evaluation cases with an actionable trace | 100 percent |

Quality thresholds may be increased after the baseline run. Hard safety thresholds cannot be waived merely to make a demo pass.

### Deliverables

- Versioned retail agent.
- Model bake-off report.
- Evaluation dataset and scorers.
- Prompt and tool policy.
- Trace/redaction configuration.
- Selected primary and fallback model decision.

### Cost controls

- Pay per token only.
- Small candidate set and bounded evaluation repetitions.
- Short prompts and compact tool payloads.
- Cache only static or aggregate, non-personal responses keyed by data version.
- No continuous evaluation.
- Rate-limit both primary and fallback endpoints.

### Exit gate

The cheapest qualifying model passes all hard gates, the agent is fully grounded in governed tools, and failure paths are safe.

---

## Phase 10 — Databricks App and wow experiences

### Objective

Deliver a polished native application that makes the model and agent capabilities understandable, credible, and memorable.

### App architecture

- Databricks App with MEDIUM compute, the smallest current app compute size.
- React/Vite front end.
- AgentServer/FastAPI back end.
- App resource bindings for SQL warehouse, serving endpoint, Lakebase, and other supported resources.
- App service principal for shared resources.
- On-behalf-of user authorization for customer-specific governed data where supported.[R11][R12]
- Pinned frontend and backend dependencies.
- Git/bundle deployment and rollback.[R13][R14]

### Screens

1. **Demo home**
   - Scenario chooser.
   - Dependency health.
   - Cost-safe demo mode indicator.

2. **Customer 360**
   - Segments, recent behavior, preferences, value signals, and privacy-safe profile facts.

3. **Retail concierge**
   - Streaming conversation.
   - Product cards.
   - Filter chips.
   - Evidence-based explanations.

4. **What-if studio**
   - Editable scenario attributes.
   - Side-by-side recommendation diff.
   - Added, removed, and reranked explanations.

5. **Semantic discovery**
   - Natural-language query.
   - Structured filters.
   - Similarity and policy evidence.

6. **Opportunity feed**
   - Replenishment.
   - Promotion relevance.
   - Cart recovery.
   - Price or inventory event.
   - Each opportunity shows the deterministic trigger and recommended next action.

7. **Quality and trace**
   - Model route.
   - Candidate-source contribution.
   - Tool activity timeline.
   - Trace ID.
   - Current model, prompt, data, and policy versions.

8. **Feedback**
   - Thumbs up/down.
   - Reason codes.
   - Optional correction.

### Wow factor principles

The wow factor must come from useful transparency and interaction, not animation alone:

- Instant known-customer results from precomputed Gold data.
- Conversational refinement backed by deterministic reranking.
- A digital-twin comparison that visibly changes the recommendation set.
- Evidence chips showing ALS/content/promotion/co-purchase sources and rule decisions.
- Semantic discovery over the real synthetic catalog.
- A live, sanitized tool timeline tied to the MLflow trace.
- A measurable cost and quality panel.

### Resilience

The app exposes:

- /health/live
- /health/ready
- /health/dependencies
- /version

If the LLM is unavailable, the app still supports deterministic search and recommendation browsing. If Lakebase is unavailable, feedback is queued or disabled with a clear message. If the dynamic endpoint is cold, the app shows progress and does not invent interim results.

### Deliverables

- Deployed Databricks App.
- Resource bindings and permission grants.
- Complete user flows.
- Health and version endpoints.
- App smoke and browser tests.
- Demo start, warm-up, stop, and rollback runbooks.

### Tests

- All primary journeys pass end to end.
- Customer isolation and OBO authorization pass.
- App identity has no raw-data privilege.
- Loading, empty, error, cold-start, and partial-dependency states render correctly.
- Product cards contain only governed tool output.
- Browser accessibility and keyboard navigation receive a basic POC check.
- A previous app deployment can be restored.

### Cost controls

- One MEDIUM app only.
- Stop the app outside active development and demo windows because Databricks Apps are billed while running.
- No duplicate preview app after acceptance.
- Static assets served by the app; no separate hosting resource.

### Exit gate

All primary journeys are polished, grounded, resilient, permission-aware, and reproducibly deployable.

---

## Phase 11 — Analytics, observability, and operations

### Objective

Make business value, model behavior, agent behavior, reliability, and cost visible in one governed operational view.

### AI/BI dashboard

Build a native dashboard with:

- Recommendation NDCG, Recall, Hit Rate, and Purchase Hit Rate.
- Customer and product coverage.
- Inventory-validity rate.
- Route mix: known, low-history, and cold-start.
- Candidate-source contribution.
- Recommendation latency and endpoint cold starts.
- Agent tool-selection and grounding scores.
- Feedback rate and positive-feedback rate.
- LLM tokens and approximate cost by model and scenario.
- SQL, job, endpoint, app, and Lakebase usage where available.
- Data freshness and failed validation count.

AI/BI dashboards are native governed assets, but their queries still consume compute.[R22] Use the one auto-stopped SQL warehouse.

### Optional curated Genie Space

A small Genie Space may expose trusted analyst questions:

- Which customer segments have the lowest recommendation hit rate?
- Which categories have high demand but low inventory?
- Which recommendation sources contribute most by segment?
- Where is cold-start feedback weakest?
- Which promotions increase recommendation eligibility?

Use certified tables, explicit instructions, trusted SQL, and benchmark questions.[R35][R36] Current documentation describes time-bounded promotional pricing for some human Genie usage; it must be rechecked immediately before activation and must not be treated as permanently free.[R37]

### Deterministic opportunity generation

Create a triggered job that writes opportunities based on transparent rules:

- Replenishment interval reached.
- Cart or browse intent without conversion.
- Relevant active promotion.
- Back-in-stock event.
- Price-change event.
- High-affinity cross-sell with inventory.

The agent explains an opportunity; it does not decide whether the trigger occurred.

### Observability

- MLflow traces for agent requests.
- Structured model-serving request metrics.
- Job run and data-quality tables.
- App health and error counters.
- Model, prompt, tool, data, feature, and policy versions on each response.
- Correlation ID from UI through agent, tool, SQL/model call, and feedback.
- Redaction of customer attributes not needed for debugging.
- Retention and sampling policy.

### Deliverables

- AI/BI dashboard.
- Optional Genie Space and benchmark.
- Opportunity job and table.
- Monitoring views.
- Alert thresholds and incident runbook.
- Cost report and resource shutdown report.

### Tests

- Dashboard figures reconcile to source tables.
- Trusted Genie questions meet the agreed answer benchmark.
- Opportunity triggers reproduce from source facts.
- Every evaluated agent request can be traced across components.
- Sensitive or unnecessary fields are absent from traces.
- Billing and usage figures have documented refresh delay and are never presented as instantaneous.

### Cost controls

- Dashboard refresh on demand or at a bounded cadence.
- No always-on warehouse.
- Genie human demo only unless current pricing is re-approved.
- Trace sampling outside evaluation runs.
- Explicit validation instead of continuous managed monitoring.

### Exit gate

Business, quality, reliability, and cost signals are governed, reconciled, and understandable to a non-technical stakeholder.

---

## Phase 12 — Release qualification and demo freeze

### Objective

Prove that the complete POC is reproducible, safe, measurable, and inexpensive to leave dormant.

### Qualification suites

#### Data

- Transfer integrity.
- Schema and reconciliation.
- Feature parity.
- Point-in-time leakage.
- Freshness and idempotency.

#### Model

- Golden inference parity.
- Baseline metric comparison.
- Inventory and eligibility.
- Coverage and diversity.
- Cold-start and fallback.
- Serialization and dependency portability.

#### Agent

- Tool selection and arguments.
- Grounding.
- Multi-turn coherence.
- Authorization.
- Prompt injection.
- Empty/error/timeout behavior.
- Cost and latency.

#### Application

- End-to-end journeys.
- Role and customer isolation.
- Dependency degradation.
- Accessibility smoke.
- Deployment rollback.

#### Platform

- Bundle deploy from clean checkout.
- Least privilege.
- No secrets in code/logs/traces.
- No resource outside the approved boundary.
- Auto-stop and scale-to-zero.
- Inventory matches live resources.

### Performance targets

Final thresholds should be calibrated with live measurements. Initial POC targets:

| Flow | Initial target |
|---|---|
| Known-customer recommendation data retrieval | P95 under 2 seconds when warehouse is warm |
| Warm dynamic recommendation inference | P95 under 5 seconds |
| Warm simple agent request | P95 under 12 seconds |
| App page shell | Interactive under 3 seconds on the client demo network |
| Scaled-to-zero dependency | Clear progress state and eventual bounded timeout |

Cold starts are reported separately and never hidden inside warm-latency metrics.

### Release packet

- Architecture and data-flow diagram.
- Live resource inventory.
- Cost estimate and observed usage.
- Test and evaluation reports.
- Model card.
- Agent card.
- Data dictionary.
- Threat model and privacy notes.
- Demo script.
- Operations, warm-up, shutdown, restore, and rollback runbooks.
- Known limitations.
- Production-gap assessment.

### Demo freeze

1. Pin data, feature, model, policy, prompt, tool, app, and bundle versions.
2. Assign the approved model alias.
3. Stop mutable development changes.
4. Run the complete qualification suite.
5. Warm only the required dependencies shortly before the demo.
6. After the demo, stop the app and allow the model endpoint, warehouse, and Lakebase to scale down.
7. Record final live resource state and observed usage.

### Exit gate

Set release status **AZURE_DATABRICKS_POC_READY** only when all hard quality, security, cost, reproducibility, and rollback gates pass. Otherwise publish a precise HOLD decision with failed gates and remediation.

---

## 11. Core contracts

## 11.1 Recommendation request

~~~json
{
  "request_id": "uuid",
  "mode": "existing_customer | new_customer | scenario",
  "customer_id": "optional-authorized-id",
  "preferences": {
    "categories": [],
    "budget_min": null,
    "budget_max": null,
    "region": null,
    "constraints": []
  },
  "scenario_overrides": {},
  "top_k": 10,
  "as_of_timestamp": "ISO-8601"
}
~~~

Rules:

- Existing mode requires an authorized customer ID.
- New mode prohibits accidental customer-history lookup.
- Scenario mode requires a base profile or explicit preference object and never persists overrides.
- top_k is bounded.
- Unknown fields are rejected.

## 11.2 Recommendation response

~~~json
{
  "request_id": "uuid",
  "route": "known | low_history | cold_start",
  "model_version": "catalog.schema.model/version",
  "policy_version": "string",
  "data_version": "string",
  "recommendations": [
    {
      "rank": 1,
      "product_id": "string",
      "score": 0.0,
      "candidate_sources": ["als", "content"],
      "reason_codes": ["category_affinity"],
      "inventory_status": "in_stock"
    }
  ],
  "generated_at": "ISO-8601",
  "warnings": []
}
~~~

## 11.3 Agent response

The app-facing agent response must distinguish:

- Natural-language text.
- Product cards sourced from a tool.
- Comparison data sourced from a tool.
- Clarification requests.
- Warnings or assumptions.
- Trace and version metadata.

The UI must not scrape product IDs or price facts from prose. Structured items are the display source of truth.

## 11.4 Version tuple

Every recommendation and evaluated agent response carries:

~~~text
transfer_version
data_version
feature_version
model_name_and_version
model_alias_at_request_time
policy_version
tool_schema_version
prompt_version
agent_code_version
app_version
~~~

This tuple makes a result reproducible and prevents the common error of evaluating an agent against moving data and model targets.

---

## 12. Security and privacy design

### Identity

- Human administration: Microsoft Entra ID identity through Azure Databricks.
- Deployment: current admin for bootstrap; workload identity for repeatable automation when approved.
- Databricks App: dedicated application service principal.
- User-specific reads: on-behalf-of authorization where supported.
- Shared operational writes: least-privilege app authorization.

### Data access

- App and agent never receive broad SELECT on Bronze, Silver, or Feature schemas.
- They read only curated serving views/functions and invoke the approved model endpoint.
- Customer context is minimized before it enters an LLM prompt.
- Direct identifiers are replaced with demo-safe identifiers where possible.
- Row filters and column masks are introduced before real client data.

### Prompt-injection boundary

Product descriptions, customer notes, tool responses, and retrieved text are untrusted data. They cannot:

- Change the system policy.
- Register a tool.
- Supply SQL identifiers.
- Reveal secrets.
- Cause a write.
- Override customer authorization.

The agent treats retrieved content as quoted evidence, and the server validates every tool call independently of the LLM.

### Secret handling

- Use Databricks secrets or supported app resource bindings.
- Never pass Azure CLI tokens to notebooks or the app.
- Never place a secret in Spark configuration that is exposed through logs.
- Redact connection fields and authorization headers in traces.
- Commit only secret names and setup instructions.

### POC limitation

The current dataset is synthetic. Before real customer data:

- Complete privacy-impact assessment.
- Classify PII and sensitive inferences.
- Establish purpose limitation and retention.
- Apply customer access controls.
- Confirm consent and marketing-policy requirements.
- Perform threat modeling and security review.

---

## 13. Cost-control operating policy

### 13.1 Before creating a resource

1. Query current live inventory.
2. Confirm resource group and region.
3. Confirm whether the capability already exists in the workspace.
4. Check Free SKU availability and subscription quota.
5. Obtain current pricing evidence for any paid capability.
6. Record expected idle and active cost behavior.
7. Define auto-stop, scale-to-zero, maximum size, token limit, and schedule state.
8. Add required tags.
9. Validate the declarative plan.
10. Create only the reviewed resource.

### 13.2 Runtime controls

| Resource | Mandatory control |
|---|---|
| SQL warehouse | Smallest size, one cluster, one-minute API auto-stop |
| Serverless jobs | Triggered; schedules paused until accepted |
| Model endpoint | Small CPU; scale-to-zero; one served model |
| LLM | Pay per token; request/token rate limits; maximum output |
| Databricks App | One MEDIUM app; manually stopped outside use |
| Lakebase | 0.5 CU minimum if available, 1 CU maximum, rapid scale-to-zero, no HA |
| Embeddings | Changed rows only; version-aware |
| Dashboard | On-demand or bounded refresh |
| Tracing | Redacted and sampled outside evaluation |
| Optional search | Free SKU only unless later approved |

### 13.3 Demo-day lifecycle

**Start**

1. Confirm cost alert state.
2. Start the Databricks App.
3. Start or query the SQL warehouse.
4. Warm the recommendation endpoint.
5. Wake Lakebase if selected.
6. Run health checks and one golden journey.

**Stop**

1. Stop the Databricks App.
2. Confirm SQL warehouse auto-stop.
3. Confirm model endpoint scale-to-zero is enabled.
4. Confirm Lakebase scale-to-zero state.
5. Leave schedules paused unless an approved next run exists.
6. Record billing-table usage after its documented refresh delay.

### 13.4 Optional-feature kill switches

Configuration defaults:

~~~yaml
features:
  lakebase_enabled: false
  azure_ai_search_free_enabled: false
  databricks_ai_search_enabled: false
  genie_enabled: false
  managed_quality_monitor_enabled: false
  detailed_inference_logging_enabled: false
  continuous_schedules_enabled: false
~~~

Each feature is enabled only after its own preflight and exit gate.

---

## 14. Evaluation strategy

### 14.1 Model evaluation

- Preserve existing offline definitions for migration comparison.
- Segment metrics by known, low-history, and cold-start.
- Report aggregate and cohort metrics.
- Add inventory validity, eligibility validity, diversity, novelty, and coverage.
- Use confidence intervals where the evaluation set supports them.
- Separate migration parity from true model-improvement experiments.
- Do not retrain or change ranker logic until Azure parity is proven.

### 14.2 Agent evaluation

Use MLflow GenAI evaluation and custom deterministic scorers for:

- Tool selection.
- Tool arguments.
- Product-ID grounding.
- Numeric grounding.
- Constraint satisfaction.
- Authorization behavior.
- Explanation completeness.
- Response helpfulness.
- Concision.
- Safe failure.

Human review should supplement, not replace, hard deterministic checks. MLflow supports evaluation, traces, scorers, and production monitoring workflows.[R33][R34]

### 14.3 Online POC feedback

Capture:

- Impression.
- Click or expand.
- Thumbs up/down.
- Feedback reason.
- Optional corrected preference.
- Scenario context.
- Model/prompt/tool versions.

Do not claim online learning. Feedback becomes an offline evaluation and future-training dataset after review.

### 14.4 Cost-quality frontier

For each LLM candidate, report:

~~~text
hard_gate_pass
task_success_rate
grounding_rate
P50_latency
P95_latency
mean_input_tokens
mean_output_tokens
observed_cost_per_successful_task
~~~

Choose the least expensive hard-gate-passing model rather than the model with the highest unconstrained subjective score.

---

## 15. POC demonstration script

Target duration: 12 to 15 minutes.

1. **Architecture and governance — 1 minute**
   - Show the Unity Catalog objects and current versions.
   - Explain the batch plus scale-to-zero serving split.

2. **Known customer — 3 minutes**
   - Open Customer 360.
   - Ask for recommendations with a budget constraint.
   - Show instant Gold results and grounded evidence.

3. **Conversational refinement — 2 minutes**
   - Add a new requirement.
   - Show tool calls and the updated eligible products.

4. **What-if studio — 3 minutes**
   - Change region, budget, or preference.
   - Compare original and simulated rankings.
   - Explain added and removed products.

5. **Semantic discovery — 2 minutes**
   - Search with a natural-language intent.
   - Show similarity plus structured filters.

6. **Opportunity and feedback — 2 minutes**
   - Open a deterministic retail opportunity.
   - Submit explicit feedback.

7. **Quality and cost — 2 minutes**
   - Show model metrics, agent grounding, trace, latency, and cost controls.
   - Show the stop/scale-down state expected after the demo.

The demo must include one graceful failure, such as an unavailable product or insufficient customer context, to prove that the agent does not hallucinate.

---

## 16. Risk register

| Risk | Impact | Mitigation | Release effect |
|---|---|---|---|
| Frozen joblib/model incompatibility | Model cannot load in serving | Dependency matrix, explicit MLflow environment, golden load tests | Hard stop |
| Old migration wrapper mistaken for real model | False success with snapshot-only inference | Replace with functional composite PyFunc and behavior tests | Hard stop |
| Incomplete transfer manifest | Missing runtime dependency | Generate from actual loader graph; remote hash verification | Hard stop |
| LLM invents product facts | Trust and safety failure | Tool-only facts, structured cards, final ID/numeric validation | Hard stop |
| Customer-data leakage | Privacy breach | OBO auth, serving views, row/column controls, adversarial tests | Hard stop |
| Scale-to-zero cold start | Poor demo experience | Warm-up runbook, progress UI, batch known-user path | Report separately |
| SQL/App left running | Avoidable cost | Auto-stop, demo stop script, inventory check | Hard stop for handoff |
| Lakebase cost or quota | Operational store unavailable | Feature flag and Delta fallback | Non-blocking if fallback passes |
| Free Azure AI Search unavailable | Optional wow feature unavailable | Delta embedding search remains default | Non-blocking |
| Databricks AI Search base cost | Unnecessary recurring cost | Do not create by default | Non-blocking |
| Foundation endpoint availability changes | Selected LLM unavailable | Bake-off against live endpoints, configured fallback | Must retest |
| Billing data delay | Misleading live cost | Label refresh delay, combine configuration caps with observed usage | Documentation gate |
| Synthetic metrics do not generalize | Overstated business value | Clearly label synthetic POC; require real holdout before production | Production blocker |
| Public network posture | Production security concern | Accept for scoped POC; plan private networking separately | Production blocker |
| Genie promotional terms change | Unexpected cost | Recheck current terms immediately before activation | Optional feature off |

---

## 17. Implementation protocol for Codex

The following protocol governs the later hands-on implementation.

### Before each phase

1. Read the latest live inventory.
2. Inspect the working tree and preserve unrelated user changes.
3. State the exact phase objective and proposed mutations.
4. Run local/unit validation.
5. Validate the bundle or CLI plan.
6. Confirm boundary and cost gates.

### During each phase

1. Prefer declarative bundle changes and versioned migrations.
2. Use Azure CLI only for Azure-level operations and the Databricks CLI/SDK or bundle for workspace objects.
3. Capture command result, resource identifier, SKU, configuration, and timestamp.
4. Never print or persist credentials.
5. Stop on a scope mismatch, unexpected paid fallback, permission expansion, or destructive requirement.
6. Keep schedules paused and optional features off until tested.

### After each phase

1. Run all phase tests.
2. Generate the evidence pack.
3. Update resource inventory and cost ledger.
4. Verify auto-stop or scale-to-zero configuration.
5. Record GO, HOLD, or ROLLBACK.
6. Commit only source, configuration, tests, and sanitized evidence.

### Mutation safety

The user has authorized implementation within the stated boundary, but that does not authorize:

- Creation outside resource group Databricks.
- Silent paid-SKU escalation.
- Destruction of an existing resource.
- Broadening identity privileges beyond the POC.
- Migration of real client PII.
- Production rollout.

These require separate, explicit decisions.

---

## 18. Definition of done

The POC is complete only when all of the following are true:

### Platform

- Bundle deploys to the existing workspace from a clean checkout.
- Every live resource appears in the resource inventory.
- All resources are inside the approved boundary.
- All compute has a bounded-use control.
- App and idle services are stopped or demonstrably scale to zero after the demo.

### Data

- The 45-file functional package is hash verified.
- Bronze-to-Gold reconciliation and data contracts pass.
- Unity Catalog lineage and privileges are visible.
- No real PII was introduced.

### Model

- The deployed MLflow artifact performs actual adaptive inference.
- Local-versus-Azure parity passes.
- Inventory validity and customer coverage meet hard gates.
- Cold-start and scenario flows pass.
- Version and rollback are documented.

### Agent

- All product and numeric claims are grounded.
- Authorization and prompt-injection suites pass.
- The least-expensive qualifying LLM is selected through evidence.
- Traces are redacted, correlated, and reproducible.
- The deterministic experience remains useful if the LLM fails.

### Application

- Known, low-history, new-customer, semantic-search, what-if, opportunity, and feedback journeys work.
- UI states cover loading, empty, cold, partial failure, and unauthorized cases.
- Health, version, warm-up, stop, and rollback procedures work.

### Evidence and handoff

- Test, evaluation, architecture, model, agent, cost, security, and operations documents are complete.
- The demo script has passed in the client-facing environment.
- Known limitations and production gaps are explicit.
- Final status is either AZURE_DATABRICKS_POC_READY or a precise HOLD.

---

## 19. Explicit non-goals for this POC

- Production SLA or 24/7 availability.
- Multi-agent architecture.
- Real-time event streaming.
- Continuous online learning.
- Large-scale vector infrastructure.
- Dedicated GPU or provisioned LLM capacity.
- Multi-region disaster recovery.
- Production PII migration.
- Marketing-message delivery to real customers.
- Power BI or separate web hosting.
- Automatic resource deletion.
- Replacing the recommendation model with an LLM.

These can be evaluated after the POC proves functional value and operating cost.

---

## 20. Production path after POC

If the POC is accepted, the next design should assess:

- Private networking, private endpoints, and outbound controls.
- Client-standard CI/CD identity and approval workflow.
- Real source-system ingestion and change data capture.
- PII classification, consent, retention, subject access, and masking.
- Representative time-based holdout and online experimentation.
- Model retraining cadence and Champion/Challenger promotion.
- Formal incident, on-call, recovery, and SLA design.
- Load-driven endpoint sizing.
- Whether a vector service is justified by catalog scale.
- Whether Lakebase should use HA or replicas.
- Central Key Vault integration if required by enterprise policy.
- Cost allocation, chargeback tags, and production budgets.
- Human review and policy ownership for outbound customer actions.

None of these should be prematurely added to the low-cost POC.

---

## 21. Official research references

All architecture claims were checked against primary Microsoft Learn, Azure Databricks, or MLflow documentation available on 2026-09-03. Product availability, limits, previews, and pricing must be rechecked immediately before deployment.

[R1]: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/
[R2]: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/bundles/resources
[R3]: https://learn.microsoft.com/en-us/azure/databricks/data-governance/
[R4]: https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/managed-versus-external
[R5]: https://learn.microsoft.com/en-us/azure/databricks/volumes/volume-files
[R6]: https://learn.microsoft.com/en-us/azure/databricks/jobs/run-serverless-jobs
[R7]: https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/create
[R8]: https://learn.microsoft.com/en-us/azure/databricks/compute/sql-warehouse/warehouse-behavior
[R9]: https://learn.microsoft.com/en-us/azure/databricks/mlflow/models
[R10]: https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/custom-models
[R11]: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/
[R12]: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/resources
[R13]: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/auth
[R14]: https://learn.microsoft.com/en-us/azure/databricks/dev-tools/databricks-apps/deploy
[R15]: https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets
[R16]: https://learn.microsoft.com/en-us/azure/databricks/agents/tutorials/agent-quickstart
[R17]: https://mlflow.org/docs/latest/genai/serving/responses-agent/
[R18]: https://mlflow.org/docs/latest/genai/serving/agent-server/
[R19]: https://learn.microsoft.com/en-us/azure/databricks/machine-learning/model-serving/foundation-model-overview
[R20]: https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/use-cases
[R21]: https://learn.microsoft.com/en-us/azure/databricks/oltp/projects/scale-to-zero
[R22]: https://learn.microsoft.com/en-us/azure/databricks/dashboards/
[R23]: https://learn.microsoft.com/en-us/azure/databricks/ai-search/ai-search
[R24]: https://learn.microsoft.com/en-us/azure/databricks/vector-search/vector-search-cost-management
[R25]: https://learn.microsoft.com/en-us/azure/search/search-try-for-free
[R26]: https://learn.microsoft.com/en-us/azure/search/search-get-started-vector
[R27]: https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/online-feature-store
[R28]: https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/
[R29]: https://learn.microsoft.com/en-us/azure/databricks/machine-learning/feature-store/time-series
[R30]: https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/data-quality-monitoring/
[R31]: https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/guardrails
[R32]: https://learn.microsoft.com/en-us/azure/databricks/ai-gateway/observability
[R33]: https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/
[R34]: https://learn.microsoft.com/en-us/azure/databricks/mlflow3/genai/eval-monitor/
[R35]: https://learn.microsoft.com/en-us/azure/databricks/genie-agents/monitor
[R36]: https://learn.microsoft.com/en-us/azure/Databricks/genie-agents/tune-quality
[R37]: https://learn.microsoft.com/en-us/azure/databricks/genie/monitor-cost

---

## 22. Immediate next action

Review the Phase 1 acceptance and proceed to **Phase 2 — Governance and platform
bootstrap** only when requested. First address scoped tags, least privilege,
Azure billing-currency verification, budget notifications to both private
recipients and tested shutdown controls. The owner budget decision is recorded,
but a budget and cost controller are not active. No paid compute or optional
service should be created before its specific cost and boundary gate passes.
