import pytest
from pydantic import ValidationError
from retail_hp_azure.serving_client import RecommendationClient, ServingError, ServingRequest


def test_invalid_request_has_no_network_call():
    with pytest.raises(ValidationError):
        ServingRequest(request_id="x", request_type="existing_customer")
    with pytest.raises(ValidationError):
        ServingRequest(request_id="x", request_type="new_customer", top_n=True)


def test_filters_stale_inventory_and_exclusions():
    rows = [
        {"request_id": "x", "product_id": p, "rank": i, "score": 0.5}
        for i, p in enumerate(["a", "b", "c"], 1)
    ]
    client = RecommendationClient(lambda payload, timeout: (200, {"predictions": rows}))
    request = ServingRequest(
        request_id="x", request_type="new_customer", excluded_product_ids_json='["b"]'
    )
    result = client.predict(request, eligible_ids={"b", "c"})
    assert [r["product_id"] for r in result] == ["c"]
    assert result[0]["rank"] == 1


def test_retry_is_bounded_and_keeps_same_request(monkeypatch):
    monkeypatch.setattr("retail_hp_azure.serving_client.time.sleep", lambda _: None)
    calls = []

    def send(payload, timeout):
        calls.append(payload)
        return 503, {}

    with pytest.raises(ServingError, match="503"):
        RecommendationClient(send).predict(
            ServingRequest(request_id="x", request_type="new_customer"), eligible_ids=set()
        )
    assert len(calls) == 2 and calls[0] == calls[1]


def test_auth_error_is_not_retried():
    calls = []

    def send(payload, timeout):
        calls.append(payload)
        return 401, {}

    with pytest.raises(ServingError, match="401"):
        RecommendationClient(send).predict(
            ServingRequest(request_id="x", request_type="new_customer"), eligible_ids=set()
        )
    assert len(calls) == 1


@pytest.mark.parametrize("excluded", ["[{}]", "[1]", "null", '{"a":1}'])
def test_excluded_ids_are_typed(excluded):
    with pytest.raises(ValidationError):
        ServingRequest(
            request_id="x", request_type="new_customer", excluded_product_ids_json=excluded
        )


@pytest.mark.parametrize(
    "rows",
    [
        None,
        [{}],
        [{"request_id": "wrong", "rank": 1, "product_id": "a", "score": 1.0}],
        [{"request_id": "x", "rank": 1, "product_id": "a", "score": float("nan")}],
        [{"request_id": "x", "rank": i, "product_id": "a", "score": 1.0} for i in (1, 2)],
    ],
)
def test_malformed_outputs_fail_closed(rows):
    client = RecommendationClient(lambda payload, timeout: (200, {"predictions": rows}))
    with pytest.raises(ServingError, match="INVALID_RESPONSE"):
        client.predict(
            ServingRequest(request_id="x", request_type="new_customer"), eligible_ids={"a"}
        )


def test_timeout_does_not_retry():
    calls = []

    def send(payload, timeout):
        calls.append(timeout)
        raise TimeoutError()

    with pytest.raises(ServingError, match="INFERENCE_TIMEOUT"):
        RecommendationClient(send).predict(
            ServingRequest(request_id="x", request_type="new_customer"), eligible_ids=set()
        )
    assert len(calls) == 1 and 0 < calls[0] <= 120


def test_stopped_warehouse_is_never_started():
    from types import SimpleNamespace

    from retail_hp_azure.serving_client import read_batch_recommendations

    warehouse = SimpleNamespace(name="retail-hp-poc-sql", state=SimpleNamespace(value="STOPPED"))
    client = SimpleNamespace(warehouses=SimpleNamespace(list=lambda: [warehouse]))
    with pytest.raises(ServingError, match="DEMO_WAREHOUSE_NOT_RUNNING"):
        read_batch_recommendations(client, "customer")
