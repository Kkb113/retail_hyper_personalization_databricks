"""Pinned Azure Luna and historical Databricks adapters with a shared fail-closed ledger."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from retail_hp_azure.config import HOST
from retail_hp_azure.phase9 import SYSTEM_PROMPT, Plan
from retail_hp_azure.safety import require

RATES = {
    "databricks-gpt-oss-20b": (1.0, 4.286),
    "databricks-meta-llama-3-1-8b-instruct": (2.143, 6.429),
}
INR_PER_DBU = 44.91  # Planning assumption, not a billed-rate guarantee.
OUTPUT_LIMIT = 1024
PHASE_LLM_ALLOWANCE = 100.0  # Leaves room for warehouse, margin, tax and storage within 250.
LUNA_ACCOUNT = "retail-hp-poc-openai-4073b6c9"
LUNA_MODEL = "gpt-5.6-luna"


def _save_ledger(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)  # Failure retains the previous reservation and fails closed.


class DatabricksPlanner:
    def __init__(self, client: Any, endpoint: str, ledger: Path) -> None:
        require(endpoint in RATES, "Unapproved foundation endpoint")
        self.client, self.endpoint, self.ledger = client, endpoint, ledger
        self.last_usage: dict[str, Any] = {}

    def connection(self) -> tuple[str, dict[str, str]]:
        require(self.client.config.host.rstrip("/") == HOST.rstrip("/"), "Workspace drift")
        return (
            f"{HOST}/serving-endpoints/{self.endpoint}/invocations",
            self.client.config.authenticate(),
        )

    def configure(self, body: dict[str, Any]) -> tuple[float, float]:
        return tuple(rate * INR_PER_DBU for rate in RATES[self.endpoint])  # type: ignore[return-value]

    def plan(self, text: str, state: dict[str, Any], *, timeout: float) -> Plan:
        return Plan.model_validate(self._request(text, state, timeout=timeout))

    def _request(
        self,
        text: str,
        state: dict[str, Any],
        *,
        timeout: float,
        schema: Any = Plan,
        prompt: str = SYSTEM_PROMPT,
        function: str = "route_retail_request",
        output_limit: int = OUTPUT_LIMIT,
        payload_limit: int = 9000,
    ) -> Any:
        import requests

        url, headers = self.connection()
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"server_state": state, "request": text})},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": function,
                        "description": "Return the requested structured retail response.",
                        "parameters": schema.model_json_schema(),
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": function}},
            "max_tokens": output_limit,
            "temperature": 0,
        }
        rates = self.configure(body)
        payload = json.dumps(body).encode()
        require(
            0 < output_limit <= 2200 and 0 < payload_limit <= 40000,
            "Invalid bounded generation configuration",
        )
        require(
            len(payload) <= payload_limit and 0 < timeout <= 30, "Request token/time bound exceeded"
        )
        reservation = (len(payload) * rates[0] + output_limit * rates[1]) / 1e6
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.ledger.with_suffix(".lock")
        # One process/session at a time. An orphan lock fails closed for operator reconciliation.
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        started = time.monotonic()
        try:
            ledger = (
                json.loads(self.ledger.read_text())
                if self.ledger.exists()
                else {
                    "reserved_or_spent_inr": 0.0,
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            )
            require(ledger["calls"] < 400, "Phase LLM call quota exhausted")
            require(
                ledger["reserved_or_spent_inr"] + reservation < PHASE_LLM_ALLOWANCE,
                "Phase LLM spending gate reached",
            )
            ledger["reserved_or_spent_inr"] += reservation
            ledger["calls"] += 1
            _save_ledger(self.ledger, ledger)
            self.last_usage = {"success": False, "reserved_inr": reservation}
            response = requests.post(
                url,
                headers=headers,
                json=body,
                timeout=(min(5, timeout), timeout),
                allow_redirects=False,
            )
            if response.status_code != 200:
                self.last_usage["http_status"] = response.status_code
                try:
                    error = response.json().get("error", {})
                    code = error.get("code", "Unknown")
                    self.last_usage["provider_error_code"] = (
                        code if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", str(code)) else "Unknown"
                    )
                    message = str(error.get("message", "")).lower()
                    if response.status_code in {401, 403}:
                        safe_message = re.sub(r"https?://\S+", "<url>", message)
                        safe_message = re.sub(r"[\w.+-]+@[\w.-]+", "<email>", safe_message)
                        safe_message = re.sub(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                            "<identifier>",
                            safe_message,
                        )
                        safe_message = re.sub(r"eyJ[\w.-]+", "<token>", safe_message)
                        self.last_usage["authentication_diagnostic"] = safe_message[:500]
                    self.last_usage["role_error"] = any(
                        text in message for text in ("role", "permission", "access denied")
                    )
                    self.last_usage["audience_error"] = "audience" in message
                except (ValueError, AttributeError):
                    pass
            require(
                response.status_code == 200 and len(response.content) <= 100000,
                f"LLM_HTTP_{response.status_code}",
            )
            result = response.json()
            usage = result.get("usage", {})
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
            require(
                type(input_tokens) is int
                and type(output_tokens) is int
                and 0 <= input_tokens <= len(payload)
                and 0 <= output_tokens <= output_limit,
                "LLM usage unavailable or outside reserved bound",
            )
            actual = (input_tokens * rates[0] + output_tokens * rates[1]) / 1e6
            ledger["reserved_or_spent_inr"] += actual - reservation
            ledger["input_tokens"] += input_tokens
            ledger["output_tokens"] += output_tokens
            _save_ledger(self.ledger, ledger)
            self.last_usage = {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_estimate_inr": actual,
                "latency_ms": (time.monotonic() - started) * 1000,
                "success": False,
            }
            choices = result.get("choices", [])
            require(
                len(choices) == 1 and choices[0].get("finish_reason") != "length",
                "LLM incomplete output",
            )
            calls = choices[0].get("message", {}).get("tool_calls", [])
            require(
                len(calls) == 1 and calls[0]["function"]["name"] == function,
                "LLM must return exactly one registered route",
            )
            plan = schema.model_validate_json(calls[0]["function"]["arguments"])
            self.last_usage["success"] = True
            return plan
        finally:
            os.close(descriptor)
            lock_path.unlink()


class AzureLunaPlanner(DatabricksPlanner):
    """Keyless Azure adapter. The host is fixed; caller supplies trusted Entra tokens.

    The same ledger MUST be used across providers. Never pass an end-user-supplied
    credential callback. Operator tokens are for validation, not app runtime identity.
    """

    def __init__(self, token_provider: Callable[[], str], ledger: Path) -> None:
        self.token_provider = token_provider
        self.endpoint, self.ledger = LUNA_MODEL, ledger
        self.last_usage: dict[str, Any] = {}

        self._last_request_started = 0.0

    def connection(self) -> tuple[str, dict[str, str]]:
        # Deployment quota is 10 RPM. Pace sequential POC calls; never retry 429s.
        delay = 6.2 - (time.monotonic() - self._last_request_started)
        if delay > 0:
            time.sleep(delay)
        token = self.token_provider()
        require(bool(token) and "\n" not in token and "\r" not in token, "Missing Entra token")
        self._last_request_started = time.monotonic()
        return (
            f"https://{LUNA_ACCOUNT}.openai.azure.com/openai/deployments/"
            f"{LUNA_MODEL}/chat/completions?api-version=2024-10-21",
            {"Authorization": "Bearer " + token},
        )

    def configure(self, body: dict[str, Any]) -> tuple[float, float]:
        body.pop("temperature")
        body["max_completion_tokens"] = body.pop("max_tokens")
        body.update(model=LUNA_MODEL, reasoning_effort="none", store=False)
        # Azure retail API, westus Global Standard short context, INR / million,
        # checked 2026-09-08. No cache discount assumed. Not an invoice guarantee.
        return 19.1093, 114.6555
