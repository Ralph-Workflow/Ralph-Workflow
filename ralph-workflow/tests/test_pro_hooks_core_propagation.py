"""Unit tests for ``apply_pro_hooks_to_core`` collaborator propagation."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.factory import PipelineCore, build_minimal_pipeline_core
from ralph.pro_support.hooks import ProPipelineHooks, apply_pro_hooks_to_core

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext


def _fake_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


def test_apply_pro_hooks_to_core_propagates_display_context() -> None:
    """A non-None ``display_context`` override is propagated into the returned core."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    new_display_context = _fake_display_context()

    result = apply_pro_hooks_to_core(core, ProPipelineHooks(display_context=new_display_context))

    assert result is not core
    assert result.display_context is new_display_context


def test_apply_pro_hooks_to_core_propagates_model_identity() -> None:
    """A non-None ``model_identity`` override is propagated into the returned core."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    new_model_identity = MagicMock()

    result = apply_pro_hooks_to_core(core, ProPipelineHooks(model_identity=new_model_identity))

    assert result.model_identity is new_model_identity


def test_apply_pro_hooks_to_core_propagates_master_prompt_materializer() -> None:
    """A non-None ``master_prompt_materializer`` override is propagated."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    new_materializer = MagicMock()

    result = apply_pro_hooks_to_core(
        core, ProPipelineHooks(master_prompt_materializer=new_materializer)
    )

    assert result.master_prompt_materializer is new_materializer


def test_apply_pro_hooks_to_core_propagates_phase_prompt_materializer() -> None:
    """A non-None ``phase_prompt_materializer`` override is propagated."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    new_materializer = MagicMock()

    result = apply_pro_hooks_to_core(
        core, ProPipelineHooks(phase_prompt_materializer=new_materializer)
    )

    assert result.phase_prompt_materializer is new_materializer


def test_apply_pro_hooks_to_core_propagates_artifact_requirements_resolver() -> None:
    """A non-None ``artifact_requirements_resolver`` override is propagated."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    new_resolver = MagicMock()

    result = apply_pro_hooks_to_core(
        core, ProPipelineHooks(artifact_requirements_resolver=new_resolver)
    )

    assert result.artifact_requirements_resolver is new_resolver


def test_apply_pro_hooks_to_core_identity_when_all_none() -> None:
    """When all core overrides are ``None``, the input core is returned unchanged."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    pro_hooks = ProPipelineHooks(
        policy_bundle_override=MagicMock(),
        registry_factory=MagicMock(),
        state_factory=MagicMock(),
        recovery_controller_factory=MagicMock(),
        marker_watcher_factory=MagicMock(),
        snapshot_registry=MagicMock(),
        recovery_sleep=MagicMock(),
    )

    result = apply_pro_hooks_to_core(core, pro_hooks)

    assert result is core


def test_apply_pro_hooks_to_core_does_not_touch_extended_fields() -> None:
    """Extended fields on ``ProPipelineHooks`` are NOT propagated to ``PipelineCore``."""
    core = build_minimal_pipeline_core(UnifiedConfig(), _fake_display_context())
    new_display_context = _fake_display_context()
    new_resolver = MagicMock()
    extended_sentinels = {
        "policy_bundle_override": MagicMock(),
        "policy_bundle_factory": MagicMock(),
        "registry_factory": MagicMock(),
        "state_factory": MagicMock(),
        "recovery_controller_factory": MagicMock(),
        "marker_watcher_factory": MagicMock(),
        "snapshot_registry": MagicMock(),
        "recovery_sleep": MagicMock(),
    }

    pro_hooks = ProPipelineHooks(
        display_context=new_display_context,
        artifact_requirements_resolver=new_resolver,
        **extended_sentinels,
    )

    result = apply_pro_hooks_to_core(core, pro_hooks)

    assert result.display_context is new_display_context
    assert result.artifact_requirements_resolver is new_resolver
    # Extended fields have no representation on PipelineCore.
    assert dataclasses.fields(PipelineCore) == tuple(_core_fields_before())


def _core_fields_before() -> list[dataclasses.Field[object]]:
    """Return the current fields of ``PipelineCore`` (used as a stable reference)."""
    return list(dataclasses.fields(PipelineCore))
