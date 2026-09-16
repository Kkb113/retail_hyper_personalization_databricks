import json
import re

import pytest
from retail_hp_azure.phase11_genie import (
    PREFIX,
    VIEWER_GROUP,
    genie_views,
    qualified_view,
    serialized_space,
    space_config,
    validate_config,
)


def test_genie_configuration_is_complete_and_deterministic():
    first = serialized_space()
    second = serialized_space()
    assert first == second
    config = json.loads(first)
    validate_config(config)
    assert config["version"] == 2
    assert len(config["data_sources"]["tables"]) == 10
    assert len(config["config"]["sample_questions"]) == 6
    assert len(config["instructions"]["text_instructions"]) == 1
    assert config["instructions"]["example_question_sqls"] == []
    assert config["benchmarks"]["questions"] == []


def test_genie_ids_are_globally_unique_lower_hex():
    config = space_config()
    items = [
        *config["config"]["sample_questions"],
        *config["instructions"]["text_instructions"],
    ]
    identifiers = [item["id"] for item in items]
    assert len(identifiers) == len(set(identifiers))
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in identifiers)


def test_genie_sources_are_aggregate_only_and_scoped():
    views = genie_views()
    assert len(views) == 4
    assert all(name.startswith("genie_") for name in views)
    assert all("GROUP BY" in sql and "SELECT customer_id" not in sql for sql in views.values())
    assert all(qualified_view(name).startswith(PREFIX + ".genie_") for name in views)
    with pytest.raises(ValueError, match="Unknown Genie view"):
        qualified_view("other")


def test_version_two_semantic_fields_do_not_use_legacy_options():
    serialized = serialized_space()
    assert "get_example_values" not in serialized
    assert "build_value_dictionary" not in serialized
    assert "enable_format_assistance" in serialized
    assert "enable_entity_matching" in serialized


def test_genie_instruction_contract_is_business_safe():
    config = space_config()
    guidance = config["instructions"]["text_instructions"][0]["content"][0]
    for requirement in (
        "business leaders",
        "synthetic and historical",
        "not recommendation accuracy",
        "not probabilities",
        "Currency is unspecified",
        "NULL means unknown",
        "Never invent",
        "practical next action",
    ):
        assert requirement.lower() in guidance.lower()
    assert VIEWER_GROUP == "retail_hp_viewers"


def test_business_only_contract_has_no_raw_sql_or_benchmarks():
    config = space_config()
    assert config["instructions"]["example_question_sqls"] == []
    assert config["benchmarks"]["questions"] == []
    assert all(
        len(item["question"][0]) > 20 and "select " not in item["question"][0].lower()
        for item in config["config"]["sample_questions"]
    )
