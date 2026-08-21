"""A turn that died at the transport must say so in the phase verdict.

Measured 2026-08-20: a Codex work unit received an inline image block,
its next Responses API request was rejected with a 400, and the CLI
emitted ``turn.failed`` and exited. The phase verdict read::

    Verdict: FAILED (no artifact) — no receipt for 'development_result';
    .agent/artifacts/development_result.md absent

Which is true and useless: it grades an infrastructure fault as an
agent-quality outcome, and the operator has no signal that the turn was
killed by an API rejection rather than by an agent that failed to write
its artifact. The terminal transport failure is right there in the raw
transcript; the verdict must fold it in, exactly as it already folds in
a raw-transcript corruption break.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport, Verbosity
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

pytestmark = pytest.mark.timeout_seconds(5)

_API_REJECTION = (
    "[400]: Invalid value: 'output_text'. Supported values are: "
    "'input_text', 'input_image', 'input_file', and 'scoped_content'."
)


def _codex_config() -> AgentConfig:
    return AgentConfig(cmd="codex", transport=AgentTransport.CODEX)


def _write_turn_failed_raw_log(workspace_root: Path) -> None:
    """Write the measured Codex shape at the path ``cmd="codex"`` resolves to."""
    raw_path = workspace_root / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "item.completed", "item": {"id": "item_57"}}),
        json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": _API_REJECTION},
            }
        ),
    ]
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_clean_raw_log(workspace_root: Path) -> None:
    raw_path = workspace_root / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps({"type": "item.completed", "item": {"id": "item_57"}}) + "\n",
        encoding="utf-8",
    )


class _StubPolicyBundle:
    pipeline = object()
    artifacts = object()


def _render_and_capture(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    shared_capture: bool = False,
) -> str:
    """Render the FAILED phase report and return the verdict line."""
    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    required_artifact = RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        artifact_path=".agent/artifacts/development_result.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )
    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="codex/gpt-5.6-terra",
        phase="development",
        prompt_file="development_prompt.md",
        drain="development",
    )
    unified_config = UnifiedConfig(
        general=GeneralConfig(),
        agents={"codex/gpt-5.6-terra": _codex_config()},
    )

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=FsWorkspace(tmp_path),
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="transport-failure-run",
        config=unified_config,
        shared_capture=shared_capture,
    )

    assert len(captured) == 1, f"expected exactly one rendered verdict line, got {captured}"
    return str(captured[0][1][1])


def test_phase_verdict_names_the_terminal_transport_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The regression: a killed turn must be distinguishable from a lazy agent."""
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" in message, message


def test_phase_verdict_quotes_the_api_rejection(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The operator needs the actual cause, not just a category."""
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "output_text" in message, message


def test_phase_verdict_omits_transport_failure_when_none_occurred(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Negative case: a clean transcript must not gain a spurious cause."""
    _write_clean_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" not in message, message


def test_phase_verdict_omits_transport_failure_when_raw_log_absent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """No transcript at all must not raise and must not invent a cause."""
    message = _render_and_capture(tmp_path, monkeypatch)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" not in message, message


def _write_recovered_turn_raw_log(workspace_root: Path) -> None:
    """A turn that failed and was followed by a successful one."""
    raw_path = workspace_root / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "turn.failed", "error": {"message": _API_REJECTION}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
    ]
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_recovered_turn_failure_is_not_reported_as_the_cause(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """A failure the unit recovered from must not be named as the cause.

    One raw capture accumulates every attempt and every phase for an
    ``(executable, model)`` pair, so an unconditional tail scan reports a
    stale frame from an attempt that already succeeded.
    """
    _write_recovered_turn_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "agent turn failed at the transport" not in message, message


def test_transport_failure_detail_contains_terminal_escapes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Transcript text is agent-influenced; it must not steer the terminal.

    The message is lifted verbatim out of the raw capture and rendered on
    the operator's terminal, so control sequences have to be stripped at
    this boundary like every other agent-origin string.
    """
    raw_path = tmp_path / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(
            {
                "type": "turn.failed",
                "error": {"message": "\x1b[2J\x1b[1;1Hwiped your screen"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "\x1b" not in message, repr(message)
    assert "wiped your screen" in message


def test_a_later_phases_verdict_does_not_inherit_an_earlier_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """One capture spans phases and parallel units; causes must not bleed.

    The raw capture is keyed only by ``(executable, model)``, so it
    accumulates every retry, every phase, and every parallel work unit
    using that agent. A failure from an earlier turn must not be
    presented as this phase's cause.
    """
    raw_path = tmp_path / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "turn.failed", "error": {"message": _API_REJECTION}}),
                # A later phase begins on the same agent and is killed
                # before writing anything of its own.
                json.dumps({"type": "turn.started"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "agent turn failed at the transport" not in message, message


def test_transport_failure_text_is_attributed_to_the_transcript(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """The capture is agent stdout, so the quote must read as a quote.

    An agent can emit a frame shaped like a transport failure. Presenting
    its text as Ralph Workflow's own finding would let it put words into
    the operator's verdict line.
    """
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "transcript reports:" in message
    assert f'"{_API_REJECTION}"' in message


def test_a_shared_capture_suppresses_transport_attribution(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Concurrent units share one capture, so no frame can be attributed.

    Parallel workers key the capture by ``(executable, model)``, so
    several interleave frames in one file. Quoting a sibling unit's API
    rejection in this unit's verdict is materially wrong -- worse than
    reporting no cause -- so attribution is suppressed for those callers.
    """
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch, shared_capture=True)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" not in message, message
