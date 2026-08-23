"""Configuration contracts for fixed conflict-resolution supervision (S-3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger
from pydantic import ValidationError

from ralph.config.loader import load_config
from ralph.config.models import UnifiedConfig


def test_default_conflict_resolution_configuration_has_fixed_documented_values() -> None:
    """S-3: all runtime conflict-routing limits come from one typed model."""
    config = UnifiedConfig.model_validate({})
    resolution = config.conflict_resolution
    assert resolution.inactivity_timeout_seconds == 900.0
    assert resolution.status_interval_seconds == 30.0
    assert resolution.max_rounds_per_stop == 3
    assert resolution.max_rebase_conflict_stops == 10
    assert resolution.max_fallback_agents == 2
    assert resolution.total_resolution_cap_seconds is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("inactivity_timeout_seconds", 0.0),
        ("status_interval_seconds", -1.0),
        ("total_resolution_cap_seconds", 0.0),
        ("total_resolution_cap_seconds", float("inf")),
        ("max_rounds_per_stop", 0),
        ("max_rebase_conflict_stops", 0),
        ("max_fallback_agents", 0),
    ],
)
def test_conflict_resolution_configuration_rejects_invalid_fields(field: str, value: object) -> None:
    """S-3: invalid supervision bounds fail during configuration validation."""
    with pytest.raises(ValidationError, match=field):
        UnifiedConfig.model_validate({"conflict_resolution": {field: value}})


def test_config_loader_warns_when_operator_enables_active_resolution_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S-3/R6: configured elapsed cap is admitted but honestly announced."""
    config_path = tmp_path / "ralph-workflow.toml"
    config_path.write_text(
        "[conflict_resolution]\ntotal_resolution_cap_seconds = 60.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("ralph.config.loader.GLOBAL_CONFIG_PATH", tmp_path / "missing-global.toml")
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        config = load_config(config_path=config_path)
    finally:
        logger.remove(sink_id)

    assert config.conflict_resolution.total_resolution_cap_seconds == 60.0
    assert [record.strip() for record in records] == [
        "conflict_resolution.total_resolution_cap_seconds is enabled: active resolution may be "
        "stopped by the operator cap"
    ]


def test_config_loader_identifies_unknown_conflict_resolution_field() -> None:
    """S-3/R10: an accidental independent bound is a load-time diagnostic."""
    from ralph.config.loader import collect_unknown_config_fields

    messages = collect_unknown_config_fields(
        {"conflict_resolution": {"inactivity_timeout_secodns": 900.0}}, Path("config.toml")
    )
    assert any("conflict_resolution.inactivity_timeout_secodns" in message for message in messages)


def test_one_agent_conflict_chain_is_warned_at_policy_load() -> None:
    """R3/B1: a one-agent conflict chain is reported when the chain is resolved."""
    from ralph.pipeline.conflict_resolution.session import resolution_chain_agents
    from ralph.policy.loader import load_policy

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        bundle = load_policy(Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults")
        agents = resolution_chain_agents(bundle)
    finally:
        logger.remove(sink_id)

    assert len(agents) == 1
    assert any("one-agent chain" in record for record in records)
