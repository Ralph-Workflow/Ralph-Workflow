"""Plumbing-level regression for the S-6 capability-breaks detector.

The plan (S-6) requires that a SUPPORTED capability claimed by an
agent whose smoke transcript contained a tool call that should have
rendered the surface produces a break in the run's ``errors`` list,
NOT a clean output. Two sub-cases:

1. **Silent when capabilities render.** A full read/write/edit
   transcript paired with a recorder pre-populated with all three
   ``_OPENCODE_CAPABILITIES`` capabilities stays silent -- the
   declared capabilities matched what the display rendered, so
   ``_detect_capability_breaks`` returns ``[]``.
2. **Fires when a SUPPORTED capability never renders.** Same
   transcript, but the recorder is empty (the S-3 "smoking gun"
   case). ``_run_smoke_agent`` must append a ``declared capability``
   break per declared SUPPORTED capability, naming each one
   verbatim.
3. **Branch A suppression.** ``support is None`` (the synthetic
   resolver returns ``None``) silences the detector even when the
   transcript clearly exercises file tools; the smoking gun is not
   in Branch A.

The test drives the real ``_run_smoke_agent`` path with a synthetic
``support_resolver`` injection (per S-5), reads the captured fixture
from ``tests/display/_fixtures/opencode_wire.jsonl`` (the 13-frame
real binary capture of the live 1.18.14 runtime, not the synthetic
bash-only sequence), and asserts on the resulting ``errors`` list.
The fake support declares the three
:class:`ralph.agents.display_capabilities.DisplayCapability` stances
with ``is_builtin=False`` (the synthetic-support path), satisfying
``_validate_display_capabilities``'s custom-agent subset rule.

The fixture is read with :meth:`Path.read_text` + :func:`str.splitlines`,
matching the pattern used in
``tests/test_opencode_captured_wire.py:test_captured_real_fixture_routes_every_tool_use``.
No real subprocess, no real wall-clock waits, no time.sleep -- so
``audit_test_policy`` flags nothing. The test is collected by
``make test`` (no markers).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.agents.builtin_spec import BuiltinAgentSpec
from ralph.agents.display_capabilities import DisplayCapability
from ralph.agents.display_capability_stance import DisplayCapabilityStance
from ralph.agents.invoke import InvokeOptions
from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.capability_observation import CapabilityObservation
from ralph.display.capability_observation_recorder import CapabilityObservationRecorder
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.support import AgentSupport


pytestmark = pytest.mark.smoke


_FIXTURE_PATH: Path = (
    Path(__file__).parent / "display" / "_fixtures" / "opencode_wire.jsonl"
)


def _read_captured_fixture_lines() -> list[str]:
    """Load the captured 1.18.14 fixture as a fresh list each call."""
    return _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


def _build_opencode_config() -> AgentConfig:
    """Build an OpenCode-shaped :class:`AgentConfig` for the regression."""
    return AgentConfig(
        cmd="opencode",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )


def _build_params(
    *,
    workspace_root: Path,
    display: ParallelDisplay | None,
    support_resolver: Callable[[str], AgentSupport | None] | None,
) -> SmokeRunParams:
    """Build a :class:`SmokeRunParams` for the regression.

    Uses the synthetic-support resolver path (no registry standing
    up), matching S-5's injection point. ``_execute_smoke_turns`` is
    monkey-patched so ``pipeline_deps`` never runs.
    """
    config = _build_opencode_config()
    relative_dir = workspace_root / "tmp" / "interactive-opencode-smoke-test"
    output_file = relative_dir / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("// smoke output\n", encoding="utf-8")
    prompt_file = workspace_root / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    return SmokeRunParams(
        agent_name="opencode/minimax/MiniMax-M3",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=workspace_root,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(force_width=120, force_glyphs=False),
        bridge=None,
        pipeline_deps=None,
        display=display,
        support_resolver=support_resolver,
    )


def _synthetic_opencode_support() -> AgentSupport:
    """Build a synthetic ``AgentSupport`` carrying the three OpenCode stances."""
    spec_record = BuiltinAgentSpec(
        transport=AgentTransport.OPENCODE,
        parser_factory=OpenCodeParser,
        strategy_factory=object(),
        json_parser=JsonParserType.OPENCODE,
        cmd="opencode",
        output_flag="--json-stream",
        can_commit=False,
        session_flag="--session {}",
        display_capabilities=(
            DisplayCapabilityStance.supported(
                DisplayCapability.SYNTAX_HIGHLIGHTING,
                detail="regression-fixture: write tool_use surfaces syntax_preview",
            ),
            DisplayCapabilityStance.supported(
                DisplayCapability.FILE_PREVIEW,
                detail="regression-fixture: read tool_use surfaces file_preview",
            ),
            DisplayCapabilityStance.supported(
                DisplayCapability.EDIT_DIFF,
                detail="regression-fixture: edit tool_use surfaces diff_preview",
            ),
        ),
    ).to_support("opencode-regression-fixture")
    return replace(spec_record, is_builtin=False, name="opencode-regression-fixture")


def _fake_execute_smoke_turns(
    _params: SmokeRunParams,
    _session_id: object,
    **_kwargs: object,
) -> tuple[list[str], list[str], None, None]:
    """Return a copy of the captured fixture as the run's transcript."""
    return list(_read_captured_fixture_lines()), [], None, None


class TestSmokeCapabilityBreaksWhenSupportedButUnrendered:
    """S-6 regression: SUPPORTED capability without render -> break."""

    def test_silent_when_capabilities_render(self, tmp_path: Path) -> None:
        """Pre-populated recorder + transcript -> no capability breaks."""
        recorder = CapabilityObservationRecorder()
        for capability in DisplayCapability:
            recorder.record(
                CapabilityObservation(
                    capability=capability,
                    tool_name="regression-fixture",
                    unit_id="opencode/minimax/MiniMax-M3",
                )
            )
        display = ParallelDisplay(
            display_context=make_display_context(force_width=120, force_glyphs=False),
            capability_recorder=recorder,
            is_quiet=True,
        )
        params = _build_params(
            workspace_root=tmp_path,
            display=display,
            support_resolver=lambda _name: _synthetic_opencode_support(),
        )
        with patch.object(
            smoke_plumbing_module,
            "_execute_smoke_turns",
            _fake_execute_smoke_turns,
        ):
            result = smoke_plumbing_module._run_smoke_agent(
                params, run_id="regression-render"
            )
        assert not any(
            err.startswith("declared capability") for err in result.errors
        ), result.errors

    def test_fires_when_supported_capability_never_renders(
        self, tmp_path: Path
    ) -> None:
        """Empty recorder + transcript -> break per declared SUPPORTED capability."""
        recorder = CapabilityObservationRecorder()
        display = ParallelDisplay(
            display_context=make_display_context(force_width=120, force_glyphs=False),
            capability_recorder=recorder,
            is_quiet=True,
        )
        params = _build_params(
            workspace_root=tmp_path,
            display=display,
            support_resolver=lambda _name: _synthetic_opencode_support(),
        )
        with patch.object(
            smoke_plumbing_module,
            "_execute_smoke_turns",
            _fake_execute_smoke_turns,
        ):
            result = smoke_plumbing_module._run_smoke_agent(
                params, run_id="regression-no-render"
            )
        errors_lower = [err.lower() for err in result.errors]
        assert any(
            "syntax_highlighting" in err and "never rendered" in err
            for err in errors_lower
        ), result.errors
        assert any(
            "file_preview" in err and "never rendered" in err
            for err in errors_lower
        ), result.errors
        assert any(
            "edit_diff" in err and "never rendered" in err
            for err in errors_lower
        ), result.errors

    def test_branch_a_suppression_when_no_support_declared(
        self, tmp_path: Path
    ) -> None:
        """``support is None`` -> Branch A silences the detector."""
        recorder = CapabilityObservationRecorder()
        display = ParallelDisplay(
            display_context=make_display_context(force_width=120, force_glyphs=False),
            capability_recorder=recorder,
            is_quiet=True,
        )
        params = _build_params(
            workspace_root=tmp_path,
            display=display,
            support_resolver=lambda _name: None,
        )
        with patch.object(
            smoke_plumbing_module,
            "_execute_smoke_turns",
            _fake_execute_smoke_turns,
        ):
            result = smoke_plumbing_module._run_smoke_agent(
                params, run_id="regression-branch-a"
            )
        assert not any(
            err.startswith("declared capability") for err in result.errors
        ), result.errors
