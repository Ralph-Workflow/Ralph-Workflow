"""Tests for the auto-integration remote-sync configuration keys.

Covers the five new ``[general]`` keys added for the opt-in remote
sync tier, with a particular focus on:

* The default-off byte-identical contract when the feature is unset or
  explicitly ``false``: no remote interaction happens and git behavior
  is unchanged.
* The standalone validation: negative intervals / negative backoff
  ceilings / negative wait seconds are rejected at the model layer.
* The deprecation warning emitted when the legacy
  ``auto_integrate_push_enabled = true`` key is set: the warning names
  the replacement so an operator can find the new flag.
* The fetch-implication rule: setting
  ``auto_integrate_remote_sync_enabled = true`` implies
  ``auto_integrate_fetch_enabled = true`` unless the operator has
  explicitly disabled the latter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.config.general_config import GeneralConfig


def _config(**overrides: object) -> GeneralConfig:
    """Build a ``GeneralConfig`` with only the given overrides.

    Defaults come from the Pydantic field defaults, so absence is the
    unchanged baseline. Empty dict means "every default", which is the
    byte-identical contract for any unmodified run.
    """
    return GeneralConfig.model_validate(overrides or {})


def test_remote_sync_defaults_are_off_and_carry_documented_values() -> None:
    """The five new keys default OFF with their documented values.

    With nothing set, the auto-integration tier is strictly local and
    the configured values match the defaults the prompt documents
    (300-second interval, 300-second backoff ceiling, 0-second wait,
    ``origin`` as the configured remote).
    """
    config = _config()
    assert config.auto_integrate_remote_sync_enabled is False
    assert config.auto_integrate_remote_target == "origin"
    assert config.auto_integrate_remote_sync_interval_seconds == 300.0
    assert config.auto_integrate_remote_backoff_max_seconds == 300.0
    assert config.auto_integrate_remote_wait_seconds == 0.0


def test_interval_zero_is_accepted_and_disables_the_throttle() -> None:
    """``0`` is the documented escape hatch: every seam gets a fetch."""
    config = _config(auto_integrate_remote_sync_interval_seconds=0.0)
    assert config.auto_integrate_remote_sync_interval_seconds == 0.0


@pytest.mark.parametrize(
    "field",
    ["auto_integrate_remote_sync_interval_seconds"],
)
def test_negative_interval_is_rejected(field: str) -> None:
    """A negative interval must fail validation, never reach the seam."""
    with pytest.raises(ValidationError):
        _config(**{field: -1.0})


def test_non_origin_remote_loads() -> None:
    """Any configured remote name is a valid target."""
    config = _config(auto_integrate_remote_target="upstream")
    assert config.auto_integrate_remote_target == "upstream"


def test_remote_sync_enabled_can_be_toggled_true() -> None:
    """Opting in is a one-line config change."""
    config = _config(auto_integrate_remote_sync_enabled=True)
    assert config.auto_integrate_remote_sync_enabled is True


def test_remote_sync_layered_precedence_local_over_global(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project-local override beats global, matching the existing contract.

    The five new keys follow the existing four-layer merge order
    (defaults -> global -> project-local -> CLI). An override at the
    project-local layer must survive a silent global layer.
    """
    from ralph.config.loader import load_toml

    global_payload = {
        "general": {
            "auto_integrate_remote_sync_enabled": True,
            "auto_integrate_remote_target": "upstream",
        }
    }
    local_payload = {
        "general": {
            "auto_integrate_remote_sync_enabled": False,
            "auto_integrate_remote_target": "origin",
        }
    }
    (tmp_path / "global.toml").write_text("")  # ignored unless we point at it
    # The deep_merge helper is the right primitive here; the layered
    # config loader merely composes it.
    from ralph.config.loader import deep_merge

    merged = deep_merge(global_payload, local_payload)
    assert merged["general"]["auto_integrate_remote_sync_enabled"] is False
    assert merged["general"]["auto_integrate_remote_target"] == "origin"
    # And the helper reads ``global.toml`` write-style too.
    sample = tmp_path / "x.toml"
    sample.write_text(
        "[general]\nauto_integrate_remote_sync_enabled = false\n"
        "auto_integrate_remote_target = \"origin\"\n"
    )
    assert load_toml(sample) == local_payload


def test_deprecated_push_enabled_emits_named_warning() -> None:
    """The deprecation warning names the replacement key.

    The warning explicitly says ``auto_integrate_remote_sync_enabled``
    so an operator reading the log line can find the new flag without
    reading the diff.
    """
    from loguru import logger

    from ralph.config.loader import _warn_deprecated_push_enabled

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        _warn_deprecated_push_enabled(
            {
                "auto_integrate_push_enabled": True,
                "auto_integrate_remote_target": "origin",
            }
        )
    finally:
        logger.remove(sink_id)
    text = "\n".join(records)
    assert "auto_integrate_remote_sync_enabled" in text
    assert "auto_integrate_remote_target" in text


def test_deprecated_push_enabled_is_silent_when_unset_or_false() -> None:
    """The warning only fires for the meaningful case.

    An unset key, or ``auto_integrate_push_enabled = false``, emits no
    warning -- the helper's contract is to call out the deprecated
    THING, not to nag every global-config load.
    """
    from loguru import logger

    from ralph.config.loader import _warn_deprecated_push_enabled

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        _warn_deprecated_push_enabled({})
        _warn_deprecated_push_enabled({"auto_integrate_push_enabled": False})
        _warn_deprecated_push_enabled({"auto_integrate_push_enabled": "yes"})  # wrong type
    finally:
        logger.remove(sink_id)
    assert not records


def test_remote_sync_implies_fetch_enabled_when_local_unset() -> None:
    """Opt-in remote sync implies the freshness probe.

    The implication is the load-time default. An operator who wants the
    observe-only refresh to stay off can still pin
    ``auto_integrate_fetch_enabled = false`` at the same layer and the
    explicit value wins.
    """
    from ralph.config.loader import _maybe_imply_fetch_enabled

    data: dict[str, object] = {
        "auto_integrate_remote_sync_enabled": True,
    }
    _maybe_imply_fetch_enabled(data)
    assert data["auto_integrate_fetch_enabled"] is True


def test_explicit_fetch_disabled_wins_over_implication() -> None:
    """An explicit ``auto_integrate_fetch_enabled = false`` short-circuits the implication."""
    from ralph.config.loader import _maybe_imply_fetch_enabled

    data: dict[str, object] = {
        "auto_integrate_remote_sync_enabled": True,
        "auto_integrate_fetch_enabled": False,
    }
    _maybe_imply_fetch_enabled(data)
    assert data["auto_integrate_fetch_enabled"] is False


def test_unset_remote_sync_does_not_imply_fetch() -> None:
    """The implication fires only when the operator has opted in."""
    from ralph.config.loader import _maybe_imply_fetch_enabled

    data: dict[str, object] = {
        "auto_integrate_remote_sync_enabled": False,
    }
    _maybe_imply_fetch_enabled(data)
    assert "auto_integrate_fetch_enabled" not in data


def test_unknown_remote_is_recorded_skip_not_a_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote named in config that is NOT configured in the repo degrades.

    The remote-sync loop never raises when the configured remote is
    absent -- it records the absence via the existing
    :data:`ralph.pipeline.auto_integrate_sync.REFRESH_NO_REMOTE` outcome
    and local-only integration proceeds. The integration contract is
    tested in the remote-sync-pull suite; this test pins the
    configuration loader never rejecting an unknown remote name.
    """
    config = GeneralConfig.model_validate(
        {
            "auto_integrate_remote_sync_enabled": True,
            "auto_integrate_remote_target": "not-configured",
        }
    )
    assert config.auto_integrate_remote_target == "not-configured"


def test_remote_wait_seconds_rejects_negative_values() -> None:
    """The wait budget is non-negative; negative values are an operator typo."""
    with pytest.raises(ValidationError):
        _config(auto_integrate_remote_wait_seconds=-1.0)


def test_remote_backoff_max_rejects_negative_values() -> None:
    """The backoff ceiling is non-negative; negative values must not slide."""
    with pytest.raises(ValidationError):
        _config(auto_integrate_remote_backoff_max_seconds=-1.0)


def test_remote_target_rejects_empty_or_whitespace_values() -> None:
    """S-2: remote publication must never silently fall back to origin."""
    for value in ("", "   "):
        with pytest.raises(ValidationError):
            _config(auto_integrate_remote_target=value)
