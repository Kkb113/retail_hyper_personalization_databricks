import pytest
from retail_hp_azure.phase6 import admit_session, endpoint_configuration, resolve_release
from retail_hp_azure.safety import SafetyError


def test_candidate_does_not_silently_become_champion():
    registry = {"owner": "retail_hp_admins", "aliases": {"candidate": 1}}
    with pytest.raises(SafetyError, match="champion"):
        resolve_release(registry)
    assert resolve_release(registry, candidate_demo_approved=True) == (1, "synthetic_demo_only")
    assert registry["aliases"] == {"candidate": 1}


def test_endpoint_is_cpu_and_scales_to_zero():
    entity = endpoint_configuration(1)["config"]["served_entities"][0]
    assert entity["workload_type"] == "CPU"
    assert entity["scale_to_zero_enabled"] is True
    assert entity["entity_version"] == "1"


def test_approved_retest_ceiling_is_explicit_and_bounded():
    reserve = admit_session(
        hourly_cost_inr=31.3392,
        active_minutes=5,
        prior_cost_inr=300,
        launch_cost_inr=31.3392,
        ceiling_inr=400,
    )
    assert 300 + reserve < 400
    with pytest.raises(SafetyError, match="headroom"):
        admit_session(
            hourly_cost_inr=31.3392, active_minutes=5, prior_cost_inr=300, launch_cost_inr=31.3392
        )
    with pytest.raises(SafetyError, match="allowance"):
        admit_session(hourly_cost_inr=31.3392, active_minutes=5, prior_cost_inr=0, ceiling_inr=401)


def test_cost_admission_includes_idle_tail_and_prior_attempts():
    assert admit_session(hourly_cost_inr=60, active_minutes=10, prior_cost_inr=0) == 80
    with pytest.raises(SafetyError, match="headroom"):
        admit_session(hourly_cost_inr=60, active_minutes=10, prior_cost_inr=180)
    with pytest.raises(SafetyError, match="finite"):
        admit_session(hourly_cost_inr=float("nan"), active_minutes=10, prior_cost_inr=0)


def test_startup_charge_is_not_ignored():
    assert (
        admit_session(hourly_cost_inr=60, active_minutes=10, prior_cost_inr=0, launch_cost_inr=10)
        == 100
    )
    with pytest.raises(SafetyError, match="headroom"):
        admit_session(hourly_cost_inr=60, active_minutes=10, prior_cost_inr=160, launch_cost_inr=10)
