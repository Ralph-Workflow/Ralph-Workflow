from ralph.skills._baseline_catalog import (
    STATIC_BUILTIN_CAPABILITIES,
    BaselineCapability,
)


def test_static_builtin_capabilities_has_five_entries() -> None:
    assert len(STATIC_BUILTIN_CAPABILITIES) == 5
    names = {cap.name for cap in STATIC_BUILTIN_CAPABILITIES}
    assert names == {"workspace_ops", "git_read_ops", "artifact_ops", "plan_read", "media_read"}


def test_static_builtin_capabilities_have_correct_tier() -> None:
    for cap in STATIC_BUILTIN_CAPABILITIES:
        assert cap.tier == "static_builtin"


def test_baseline_capability_is_frozen_dataclass() -> None:
    cap = BaselineCapability("test", "description", "static_builtin")
    assert cap.name == "test"
    assert cap.description == "description"
    assert cap.tier == "static_builtin"
