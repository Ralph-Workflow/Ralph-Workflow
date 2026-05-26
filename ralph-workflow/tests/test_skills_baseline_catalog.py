"""Tests for ralph.skills._baseline_catalog."""

from ralph.skills._baseline_catalog import (
    CONDITIONAL_DEFAULTS,
    MANDATORY_DEFAULTS,
    NON_DEFAULTS,
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


def test_mandatory_defaults_has_three_entries() -> None:
    assert len(MANDATORY_DEFAULTS) == 3
    names = {cap.name for cap in MANDATORY_DEFAULTS}
    assert names == {"web_search", "visit_url", "skills_bundle"}


def test_mandatory_defaults_have_correct_tier() -> None:
    for cap in MANDATORY_DEFAULTS:
        assert cap.tier == "mandatory"


def test_skills_bundle_description_mentions_mirrored_upstream_bundle() -> None:
    skills_bundle = next(cap for cap in MANDATORY_DEFAULTS if cap.name == "skills_bundle")
    assert "mirrored" in skills_bundle.description
    assert "upstream" in skills_bundle.description


def test_conditional_defaults_has_one_entry() -> None:
    assert len(CONDITIONAL_DEFAULTS) == 1
    assert CONDITIONAL_DEFAULTS[0].name == "docs_mcp"
    assert CONDITIONAL_DEFAULTS[0].tier == "conditional"


def test_non_defaults_has_four_entries() -> None:
    assert len(NON_DEFAULTS) == 4
    names = {cap.name for cap in NON_DEFAULTS}
    assert names == {"github", "playwright", "crawl4ai", "exa_search"}


def test_non_defaults_have_correct_tier() -> None:
    for cap in NON_DEFAULTS:
        assert cap.tier == "non_default"


def test_baseline_capability_is_frozen_dataclass() -> None:
    cap = BaselineCapability("test", "description", "mandatory")
    assert cap.name == "test"
    assert cap.description == "description"
    assert cap.tier == "mandatory"
