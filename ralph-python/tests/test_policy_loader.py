"""Unit tests for :mod:`ralph.policy.loader`."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from ralph.policy import loader as policy_loader
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    _format_validation_error_detail,
    _format_validation_error_messages,
    _format_validation_location,
    _format_validation_message,
    load_policy,
    load_policy_or_die,
)
from ralph.policy.validation import PolicyValidationError as PolicyContractValidationError


class _DummyValidationError:
    def __init__(self, errors: list[dict[str, object]]) -> None:
        self._errors = errors

    def errors(self) -> list[dict[str, object]]:
        return self._errors


def _copy_default_policy_files(target_dir: Path) -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("agents.toml", "pipeline.toml", "artifacts.toml"):
        shutil.copy(defaults_dir / filename, target_dir / filename)


def test_format_validation_helpers_handle_various_inputs() -> None:
    detail: dict[str, object] = {"loc": ["agents", "chain"], "msg": "missing chain"}
    assert _format_validation_error_detail(detail) == "  agents.chain: missing chain"
    assert _format_validation_location(None) == "<root>"
    assert _format_validation_location([]) == "<root>"
    assert _format_validation_location("top") == "top"
    assert _format_validation_message(None) == "<missing message>"
    assert _format_validation_message(42) == "42"

    dummy = _DummyValidationError([detail, {"loc": None, "msg": "oops"}])
    messages = _format_validation_error_messages(cast("Any", dummy))
    assert messages == [
        "  agents.chain: missing chain",
        "  <root>: oops",
    ]


def test_load_policy_invalid_toml_raises(tmp_path: Path) -> None:
    (tmp_path / "agents.toml").write_text("not a valid toml: <<<")
    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "Failed to parse TOML" in excinfo.value.message
    assert excinfo.value.source == "agents.toml"


def test_load_policy_reports_agent_validation_failure(tmp_path: Path) -> None:
    (tmp_path / "agents.toml").write_text("[agent_chains.planning]\nagents = []\n")
    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(tmp_path)
    assert "agents.toml validation failed" in excinfo.value.message
    assert excinfo.value.source == "agents"


def test_load_policy_wraps_validate_drain_contracts_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_dir = tmp_path / "policy"
    _copy_default_policy_files(config_dir)

    def fake_validate(_: object) -> None:
        raise PolicyContractValidationError("drain contract failure")

    monkeypatch.setattr(policy_loader, "validate_drain_contracts", fake_validate)

    with pytest.raises(LoaderPolicyValidationError) as excinfo:
        load_policy(config_dir)
    assert excinfo.value.message == "drain contract failure"
    assert excinfo.value.source == "agents"


def test_load_policy_or_die_exits_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_load(_: Path) -> None:
        raise LoaderPolicyValidationError("boom", source="agents")

    mock_logger = MagicMock()
    monkeypatch.setattr(policy_loader, "load_policy", fake_load)
    monkeypatch.setattr(policy_loader, "logger", mock_logger)

    with pytest.raises(SystemExit) as excinfo:
        load_policy_or_die(Path("unused"))
    assert excinfo.value.code == 1

    expected_messages: list[tuple[str, str]] = [
        ("Policy validation failed: {}", "boom"),
        ("  Source: {}", "agents"),
    ]
    assert mock_logger.error.call_count == len(expected_messages)
    for idx, (fmt, value) in enumerate(expected_messages):
        assert mock_logger.error.call_args_list[idx][0][0] == fmt
        assert mock_logger.error.call_args_list[idx][0][1] == value
