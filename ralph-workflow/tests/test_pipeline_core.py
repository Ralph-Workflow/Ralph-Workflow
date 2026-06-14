"""Unit tests for the modular ``PipelineCore`` surface and its factory."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.factory import (
    PipelineCore,
    PipelineDeps,
    _materialize_prompt_for_phase,
    _materialize_system_prompt,
    _resolve_phase_required_artifact,
    build_default_pipeline_deps,
    build_minimal_pipeline_core,
)
from ralph.pro_support.hooks import ProPipelineHooks, apply_pro_hooks_to_core

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext


def _fake_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


def test_pipeline_core_constructed_with_all_collaborators() -> None:
    """``PipelineCore`` can be constructed with all four PROMPT collaborators."""
    display_context = _fake_display_context()
    model_identity = MagicMock()
    system_prompt_materializer = MagicMock()
    phase_prompt_materializer = MagicMock()
    artifact_requirements_resolver = MagicMock()

    core = PipelineCore(
        display_context=display_context,
        model_identity=model_identity,
        system_prompt_materializer=system_prompt_materializer,
        phase_prompt_materializer=phase_prompt_materializer,
        artifact_requirements_resolver=artifact_requirements_resolver,
    )

    assert core.display_context is display_context
    assert core.model_identity is model_identity
    assert core.system_prompt_materializer is system_prompt_materializer
    assert core.phase_prompt_materializer is phase_prompt_materializer
    assert core.artifact_requirements_resolver is artifact_requirements_resolver


def test_pipeline_core_is_frozen_dataclass() -> None:
    """``PipelineCore`` is immutable; mutation raises ``FrozenInstanceError``."""
    core = PipelineCore(display_context=_fake_display_context())
    mutable_core: Any = core

    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_core.model_identity = MagicMock()


def test_build_minimal_pipeline_core_wires_production_defaults() -> None:
    """``build_minimal_pipeline_core`` returns a ``PipelineCore`` with production defaults."""
    config = UnifiedConfig()
    display_context = _fake_display_context()

    core = build_minimal_pipeline_core(config, display_context)

    assert isinstance(core, PipelineCore)
    assert core.display_context is display_context
    assert core.model_identity is None
    assert core.system_prompt_materializer is _materialize_system_prompt
    assert core.phase_prompt_materializer is _materialize_prompt_for_phase
    assert core.artifact_requirements_resolver is _resolve_phase_required_artifact


def test_build_minimal_pipeline_core_forwards_model_identity() -> None:
    """``build_minimal_pipeline_core`` forwards an injected ``model_identity``."""
    config = UnifiedConfig()
    display_context = _fake_display_context()
    model_identity = MagicMock()

    core = build_minimal_pipeline_core(
        config, display_context, model_identity=model_identity
    )

    assert core.model_identity is model_identity


def test_build_minimal_pipeline_core_rejects_pro_hooks() -> None:
    """``build_minimal_pipeline_core`` does not accept extended fields like ``pro_hooks``."""
    config = UnifiedConfig()
    display_context = _fake_display_context()

    def _call_with_pro_hooks(**kwargs: object) -> object:
        return build_minimal_pipeline_core(config, display_context, **kwargs)

    with pytest.raises(TypeError):
        _call_with_pro_hooks(pro_hooks=MagicMock())


def test_apply_pro_hooks_to_core_propagates_collaborators() -> None:
    """``apply_pro_hooks_to_core`` propagates the four PROMPT collaborators."""
    core = PipelineCore(display_context=_fake_display_context())
    new_display_context = _fake_display_context()
    new_model_identity = MagicMock()
    new_system_prompt_materializer = MagicMock()
    new_phase_prompt_materializer = MagicMock()
    new_artifact_requirements_resolver = MagicMock()

    pro_hooks = ProPipelineHooks(
        display_context=new_display_context,
        model_identity=new_model_identity,
        system_prompt_materializer=new_system_prompt_materializer,
        phase_prompt_materializer=new_phase_prompt_materializer,
        artifact_requirements_resolver=new_artifact_requirements_resolver,
    )

    overridden = apply_pro_hooks_to_core(core, pro_hooks)

    assert overridden is not core
    assert overridden.display_context is new_display_context
    assert overridden.model_identity is new_model_identity
    assert overridden.system_prompt_materializer is new_system_prompt_materializer
    assert overridden.phase_prompt_materializer is new_phase_prompt_materializer
    assert overridden.artifact_requirements_resolver is new_artifact_requirements_resolver


def test_apply_pro_hooks_to_core_identity_when_all_none() -> None:
    """``apply_pro_hooks_to_core`` returns the input core when all overrides are ``None``."""
    core = PipelineCore(display_context=_fake_display_context())
    pro_hooks = ProPipelineHooks()

    result = apply_pro_hooks_to_core(core, pro_hooks)

    assert result is core


def test_pipeline_deps_importable_and_is_dataclass() -> None:
    """``PipelineDeps`` remains importable and is still a dataclass."""
    assert dataclasses.is_dataclass(PipelineDeps)


def test_pipeline_deps_has_embedded_core() -> None:
    """``PipelineDeps`` embeds a ``PipelineCore`` accessible via the ``core`` field."""
    config = UnifiedConfig()
    display_context = _fake_display_context()

    deps = build_default_pipeline_deps(config, display_context)

    assert isinstance(deps.core, PipelineCore)
    assert deps.core.display_context is display_context
    assert deps.core.model_identity is None


def test_pipeline_deps_backward_compat_properties_read_from_core() -> None:
    """``PipelineDeps`` exposes the four collaborators via backward-compat properties."""
    config = UnifiedConfig()
    display_context = _fake_display_context()
    model_identity = MagicMock()

    deps = build_default_pipeline_deps(
        config, display_context, model_identity=model_identity
    )

    assert deps.display_context is display_context
    assert deps.model_identity is model_identity
    assert deps.system_prompt_materializer is deps.core.system_prompt_materializer
    assert deps.phase_prompt_materializer is deps.core.phase_prompt_materializer
    assert deps.artifact_requirements_resolver is deps.core.artifact_requirements_resolver
