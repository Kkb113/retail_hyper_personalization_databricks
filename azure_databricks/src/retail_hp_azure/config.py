"""Strict POC contract. A budget is a target, not an Azure billing hard cap."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

HOST = "https://adb-7405618180989330.10.azuredatabricks.net"
ROOT_PATH = "/Workspace/Users/${workspace.current_user.userName}/.bundle/retail-hp-azure/poc"
CLI_VERSION = "1.15.0"
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
ResourceName = Annotated[str, Field(pattern=r"^retail-hp-[a-z0-9-]{1,48}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def literal_types(cls, data: Any) -> Any:
        # Python considers False == 0 and True == 1; policy JSON must not.
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                if name in data and get_origin(field.annotation) is Literal:
                    expected_type = type(get_args(field.annotation)[0])
                    if type(data[name]) is not expected_type:
                        raise ValueError("Policy literals must use their exact JSON type")
        return data


class Scope(StrictModel):
    resource_group: Literal["Databricks"]
    workspace: Literal["intellify-databricks-demo"]
    host: Literal["https://adb-7405618180989330.10.azuredatabricks.net"]
    region: Literal["westus"]
    catalog: Literal["intellify_databricks_demo"]


class Names(StrictModel):
    bronze_schema: Name
    silver_schema: Name
    gold_schema: Name
    ml_schema: Name
    warehouse: ResourceName
    model_endpoint: ResourceName
    app: ResourceName


class Features(StrictModel):
    jobs: Literal[False]
    sql_warehouse: Literal[False]
    model_serving: Literal[False]
    llm: Literal[False]
    app: Literal[False]
    lakebase: Literal[False]
    vector_search: Literal[False]
    azure_ai_search: Literal[False]


class Cost(StrictModel):
    currency: Literal["INR"]
    monthly_target: Literal[12000]
    internal_stop_target: Literal[9000]
    reserve: Literal[3000]
    azure_hard_cap_available: Literal[False]
    budget_deployed: Literal[False]
    shutdown_controller_deployed: Literal[False]
    notification_recipient_count: Literal[2]
    notification_recipients_in_git: Literal[False]
    free_to_paid_fallback: Literal[False]
    paid_resource_creation_allowed: Literal[False]
    cloud_mutations_allowed: Literal[False]
    idle_policy: Literal["service_specific_session_bounded"]
    human_idle_reference_minutes: Literal[20]
    hard_idle_limit_required: Literal[False]
    sql_auto_stop_minutes: Literal[1]
    custom_serving_blocked_by_idle_policy: Literal[False]
    demonstrations_per_month_max: Literal[4]


class PocConfig(StrictModel):
    version: Literal["azure_phase1_v1"]
    phase: Literal[1]
    target: Literal["poc"]
    scope: Scope
    names: Names
    features: Features
    cost: Cost
    tags: dict[str, str]


def load_config(path: Path) -> PocConfig:
    return PocConfig.model_validate_json(path.read_text(encoding="utf-8"))


def schema_text() -> str:
    return json.dumps(PocConfig.model_json_schema(), indent=2, sort_keys=True) + "\n"
