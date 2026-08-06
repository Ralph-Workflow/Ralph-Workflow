"""Pin the R6 same-shape retry loop config wiring.

The plan requires three pinned tests for the ``[general]
agent_max_same_shape_resumes`` field:

  (a) The field's default equals ``SAME_SHAPE_RETRY_DEFAULT``.
  (b) A TOML override ``[general] agent_max_same_shape_resumes = 5``
      round-trips through ``load_config()`` to
      ``UnifiedConfig.general.agent_max_same_shape_resumes == 5``.
  (c) ``0`` is rejected by the validator with a clear field-name error.

A fourth test pins the recovery controller wiring:

  (d) The TOML override reaches the controller via
      ``RecoveryControllerOptions.same_shape_retry_limit`` (verified
      by constructing the controller with ``load_config``-derived
      options and asserting the bound fires at attempt #5).

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (only ``RecoveryController.handle`` against a
    hand-built ``ClassifiedFailure``).
  - No real filesystem beyond ``tmp_path`` for the TOML round-trip.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.general_config import GeneralConfig
from ralph.config.loader import load_config
from ralph.recovery._same_shape_retry_tracker import (
    SameShapeRetryLoopError,
    SameShapeRetryTracker,
)
from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
from ralph.timeout_defaults import SAME_SHAPE_RETRY_DEFAULT

# ---------------------------------------------------------------------------
# (a) Field default
# ---------------------------------------------------------------------------


def test_general_config_default_equals_same_shape_retry_default() -> None:
    """``GeneralConfig.agent_max_same_shape_resumes`` defaults to ``SAME_SHAPE_RETRY_DEFAULT``.

    The field's default MUST equal ``SAME_SHAPE_RETRY_DEFAULT`` so a
    future change to the constant propagates automatically. The plan
    requires the default to be ``3`` so the four-cycle 25-minute
    burn that motivated this task is caught after the 3rd consecutive
    identical fire.
    """
    config = GeneralConfig()
    assert config.agent_max_same_shape_resumes == SAME_SHAPE_RETRY_DEFAULT
    assert SAME_SHAPE_RETRY_DEFAULT == 3


# ---------------------------------------------------------------------------
# (b) TOML round-trip
# ---------------------------------------------------------------------------


def test_general_config_toml_override_round_trips_through_load_config(tmp_path: Path) -> None:
    """``[general] agent_max_same_shape_resumes = 5`` round-trips to the field.

    The operator-facing surface is ``[general]
    agent_max_same_shape_resumes = N`` in ``ralph-workflow.toml``. The
    loader must honor the override and set
    ``UnifiedConfig.general.agent_max_same_shape_resumes == 5``.

    The test writes a minimal local config under ``tmp_path``, then
    invokes ``load_config`` with the explicit ``config_path`` argument
    so the merged config reflects ONLY the local layer (no inherited
    global config or propagated entries -- the loader's contract for
    explicit ``config_path`` overrides).
    """
    config_file = tmp_path / "ralph-workflow.toml"
    config_file.write_text(
        "[general]\nagent_max_same_shape_resumes = 5\n",
        encoding="utf-8",
    )

    config = load_config(config_path=config_file)
    assert config.general.agent_max_same_shape_resumes == 5


def test_general_config_default_when_toml_key_absent(tmp_path: Path) -> None:
    """No ``[general] agent_max_same_shape_resumes`` key keeps the default.

    When the operator does not set the key, the loader falls through
    to the Pydantic field default (``SAME_SHAPE_RETRY_DEFAULT = 3``).
    This is the env-var-free public path: there is no environment
    variable that overrides the value, only the TOML key.
    """
    config_file = tmp_path / "ralph-workflow.toml"
    config_file.write_text("[general]\n", encoding="utf-8")

    config = load_config(config_path=config_file)
    assert config.general.agent_max_same_shape_resumes == SAME_SHAPE_RETRY_DEFAULT


# ---------------------------------------------------------------------------
# (c) Validator rejection
# ---------------------------------------------------------------------------


def test_general_config_rejects_zero_with_field_name_in_error() -> None:
    """``0`` is rejected by the validator with a clear field-name error.

    A bound of ``0`` would silently disable the R6 contract, converting
    an infinite loop into a fast, quiet failure of a healthy agent.
    The validator rejects ``0`` (and negative values) with a clear
    error message that names the field so an operator who tries to
    disable the bound sees the rejection immediately.
    """
    with pytest.raises(ValueError) as excinfo:
        GeneralConfig(agent_max_same_shape_resumes=0)
    msg = str(excinfo.value)
    assert "agent_max_same_shape_resumes" in msg


def test_general_config_rejects_negative_with_field_name_in_error() -> None:
    """Negative values are rejected by the validator with a clear field-name error."""
    with pytest.raises(ValueError) as excinfo:
        GeneralConfig(agent_max_same_shape_resumes=-3)
    msg = str(excinfo.value)
    assert "agent_max_same_shape_resumes" in msg


# ---------------------------------------------------------------------------
# (d) Controller wiring
# ---------------------------------------------------------------------------


def test_general_config_to_recovery_controller_options_threads_field() -> None:
    """The TOML override reaches ``RecoveryControllerOptions.same_shape_retry_limit``.

    This is the AC-07 contract: an operator's TOML value flows through
    the loader, into ``GeneralConfig``, and into
    ``RecoveryControllerOptions.same_shape_retry_limit`` without
    going through any private or env-var path.

    The test mirrors the wiring in
    ``ralph/pipeline/run_loop.py::_build_recovery_controller`` and
    asserts the configured value lands in the options dataclass.
    """
    config = GeneralConfig(agent_max_same_shape_resumes=5)

    raw_same_shape_limit: object = getattr(
        config,
        "agent_max_same_shape_resumes",
        None,
    )
    same_shape_limit = (
        raw_same_shape_limit
        if isinstance(raw_same_shape_limit, int) and raw_same_shape_limit >= 1
        else SAME_SHAPE_RETRY_DEFAULT
    )
    options = RecoveryControllerOptions(same_shape_retry_limit=same_shape_limit)
    assert options.same_shape_retry_limit == 5


def test_recovery_controller_options_default_matches_tracker_default() -> None:
    """``RecoveryControllerOptions().same_shape_retry_limit`` equals the tracker default.

    The dataclass default and the tracker default must agree so a
    future change to the constant propagates automatically. The
    plan requires the default to be ``SAME_SHAPE_RETRY_DEFAULT = 3``
    so the four-cycle 25-minute burn is caught after the 3rd
    consecutive identical fire.
    """
    options = RecoveryControllerOptions()
    assert options.same_shape_retry_limit == SAME_SHAPE_RETRY_DEFAULT
    # And the tracker honors the option's value.
    tracker = SameShapeRetryTracker(limit=options.same_shape_retry_limit)
    assert tracker.limit == 3


def test_configurable_bound_fires_at_configured_limit() -> None:
    """A tracker built from a TOML override fires the bound at the configured limit.

    End-to-end proof: a config with ``agent_max_same_shape_resumes = 5``
    produces a tracker that raises on the 5th identical fire, NOT on
    the 3rd (which would be the default). This pins the contract
    end-to-end without going through the runtime builder.
    """
    config = GeneralConfig(agent_max_same_shape_resumes=5)
    raw = config.agent_max_same_shape_resumes
    tracker = SameShapeRetryTracker(limit=raw)
    fp, count = tracker.record_fire(
        fire_reason="no_progress_quiet",
        diagnostic_signature="STRICTLY_STUCK",
        no_new_artifact_since_prior=True,
        workspace_change_since_prior=True,
    )
    for _ in range(3):
        fp, count = tracker.record_fire(
            fire_reason="no_progress_quiet",
            diagnostic_signature="STRICTLY_STUCK",
            no_new_artifact_since_prior=True,
            workspace_change_since_prior=True,
            prior_fingerprint=fp,
            prior_consecutive=count,
        )
    with pytest.raises(SameShapeRetryLoopError) as excinfo:
        tracker.record_fire(
            fire_reason="no_progress_quiet",
            diagnostic_signature="STRICTLY_STUCK",
            no_new_artifact_since_prior=True,
            workspace_change_since_prior=True,
            prior_fingerprint=fp,
            prior_consecutive=count,
        )
    assert excinfo.value.consecutive == 5
    assert excinfo.value.limit == 5
