"""Explicit OBO runtime wiring; never falls back to an operator or developer login."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from retail_hp_azure.config import HOST
from retail_hp_azure.demo_cohort import CustomerDirectoryUnavailable
from retail_hp_azure.phase6 import ENDPOINT_NAME
from retail_hp_azure.phase8 import GovernedTools, ToolContext
from retail_hp_azure.phase8_backend import DatabricksToolBackend, EmbeddingClient
from retail_hp_azure.phase8_semantic import SemanticIndex
from retail_hp_azure.phase9 import RetailAgent
from retail_hp_azure.phase10_conversation import ConversationAgent, ConversationalPlanner
from retail_hp_azure.safety import require

SERVICE_CREDENTIAL = "retail_hp_luna_app"


class RunningBackend(DatabricksToolBackend):
    """Permit bounded warehouse wake-up only inside an authorized demo lease."""

    lease_expires: float = 0

    def _query(self, statement: str, values: dict[str, Any]) -> list[dict[str, Any]]:
        self.check_running()
        return super()._query(statement, values)

    def check_running(self) -> None:
        warehouse = self.client.warehouses.get(self.warehouse_id)
        require(warehouse.name == "retail-hp-poc-sql", "Warehouse identity drift")
        require(warehouse.state is not None, "WAREHOUSE_UNAVAILABLE")
        if self.lease_expires and warehouse.state.value != "RUNNING":
            require(time.time() < self.lease_expires - 75, "DEMO_WINDOW_ENDING")
            stop_wait = time.monotonic() + 40
            requested = False
            while warehouse.state.value != "RUNNING":
                require(
                    time.time() < self.lease_expires - 60 and time.monotonic() < stop_wait,
                    "WAREHOUSE_WARMING",
                )
                require(
                    warehouse.state.value in {"STOPPED", "STOPPING", "STARTING"},
                    "WAREHOUSE_UNAVAILABLE",
                )
                if warehouse.state.value == "STOPPED" and not requested:
                    self.client.warehouses.start(self.warehouse_id)
                    requested = True
                time.sleep(2)
                warehouse = self.client.warehouses.get(self.warehouse_id)
        require(
            warehouse.name == "retail-hp-poc-sql"
            and warehouse.state is not None
            and warehouse.state.value == "RUNNING",
            "WAREHOUSE_NOT_EXPLICITLY_RUNNING",
        )

    def write_feedback(self, event: Any) -> Any:
        self.check_running()
        return super().write_feedback(event)


class WorkbenchRuntime:
    """App M2M is used only for Luna credentials and dependency metadata.

    Customer/operational SQL uses the caller's verified OBO token. Entitlements
    are a deployment-owned mapping keyed by SCIM ID, never an HTTP parameter.
    A single App instance/worker is required by Phase 7's single-writer POC store.
    """

    def __init__(
        self,
        *,
        app_client: Any,
        warehouse_id: str,
        entitlements: dict[str, ToolContext],
        actor_secret: bytes,
        ledger: Path,
        index: SemanticIndex | None,
        lease_expires: float,
        trace_sink: Callable[[dict[str, Any]], None],
        user_client_factory: Callable[[str], Any] | None = None,
        cohort_subjects: frozenset[str] = frozenset(),
    ) -> None:
        require(app_client.config.host.rstrip("/") == HOST, "Workspace scope drift")
        require(app_client.config.auth_type == "oauth-m2m", "App must use native workload OAuth")
        require(0 < len(entitlements) <= 20, "Explicit operator entitlements required")
        require(
            all(key == ctx.subject for key, ctx in entitlements.items()), "Identity mapping drift"
        )
        require(time.time() < lease_expires <= time.time() + 1800, "Bounded demo lease required")
        require(len(actor_secret) >= 32, "Persistent actor secret required")
        self.app_client, self.warehouse_id = app_client, warehouse_id
        self.entitlements, self.actor_secret = entitlements, actor_secret
        require(cohort_subjects <= entitlements.keys(), "Unknown cohort actor")
        self.cohort_subjects = cohort_subjects
        self._cohort_lock = threading.Lock()
        self._cohort_reads = 0
        self.index, self.lease_expires, self.trace_sink = index, lease_expires, trace_sink
        self.user_client_factory = user_client_factory or self._user_client
        self.planner = ConversationalPlanner(self._luna_token, ledger)
        self.planner.usage_sink = trace_sink
        self.planner.allowance_inr = 25.0  # Shared testing + owner chat; no per-request allowance.

    @staticmethod
    def _user_client(token: str) -> Any:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        return WorkspaceClient(
            config=Config(host=HOST, token=token, auth_type="pat", http_timeout_seconds=30)
        )

    def _admit(self) -> None:
        require(time.time() < self.lease_expires, "Demo lease expired")

    def authenticate(self, token: str, *, metadata_only: bool = False) -> ToolContext:
        self._admit()
        client = self.user_client_factory(token)
        identity = client.current_user.me()
        require(bool(identity.id) and identity.active is True, "Inactive or missing identity")
        require(identity.id in self.entitlements, "User is not on the demo allowlist")
        context = self.entitlements[str(identity.id)]
        if not metadata_only and str(identity.id) in self.cohort_subjects:
            with self._cohort_lock:
                require(self._cohort_reads < 200, "Cohort lookup allowance exhausted")
                self._cohort_reads += 1  # Reserve before SQL; failures still count.
            # Resolve from the current publication using the caller's identity.
            # Never infer grants from a numeric ID range or use App credentials.
            backend = RunningBackend(client, self.warehouse_id)
            backend.lease_expires = self.lease_expires
            try:
                customers = backend.ready_customers()
            except Exception as exc:
                raise CustomerDirectoryUnavailable(
                    "Customer list is temporarily unavailable"
                ) from exc
            context = context.model_copy(update={"allowed_customers": frozenset(customers)})
        return context

    def _luna_token(self) -> str:
        from databricks.sdk.service.catalog import GenerateTemporaryServiceCredentialAzureOptions

        self._admit()
        temporary = self.app_client.credentials.generate_temporary_service_credential(
            SERVICE_CREDENTIAL,
            azure_options=GenerateTemporaryServiceCredentialAzureOptions(
                resources=["https://ai.azure.com/.default"]
            ),
        )
        require(
            temporary.expiration_time is not None
            and temporary.expiration_time > (time.time() + 60) * 1000,
            "Luna workload token expired",
        )
        require(
            temporary.azure_aad is not None and bool(temporary.azure_aad.aad_token),
            "Luna workload token unavailable",
        )
        assert temporary.azure_aad is not None
        return str(temporary.azure_aad.aad_token)

    def dependencies(self) -> dict[str, str]:
        self._admit()
        warehouse = self.app_client.warehouses.get(self.warehouse_id)
        endpoint = self.app_client.api_client.do(
            "GET", f"/api/2.0/serving-endpoints/{ENDPOINT_NAME}"
        )
        return {
            "warehouse": warehouse.state.value if warehouse.state else "UNKNOWN",
            "recommender": str(endpoint.get("state", {}).get("suspend", "UNKNOWN")),
            "semantic_snapshot": "loaded" if self.index else "unavailable",
            "luna": "configured; invocation not checked by health probe",
            "feedback": "Delta; requires authorized running warehouse",
        }

    def tools(self, token: str) -> GovernedTools:
        self._admit()
        client = self.user_client_factory(token)
        backend = RunningBackend(
            client,
            self.warehouse_id,
            index=self.index,
            embeddings=EmbeddingClient(client, max_calls=1),
        )
        backend.lease_expires = self.lease_expires
        return GovernedTools(
            backend,
            actor_secret=self.actor_secret,
            trace_sink=self.trace_sink,
        )

    def agent(self, token: str) -> RetailAgent:
        self._admit()
        return ConversationAgent(self.planner, self.tools(token), self.trace_sink)

    lease_expires: float = 0
