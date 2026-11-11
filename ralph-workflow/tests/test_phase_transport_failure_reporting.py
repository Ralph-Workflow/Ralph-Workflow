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


def _capture_path(root: Path, config: AgentConfig | None = None) -> Path:
    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    resolved = config or _codex_config()
    return raw_log_path_for(root, raw_log_unit_id_for(resolved), model=resolved.model)


def _write_turn_failed_raw_log(workspace_root: Path) -> None:
    raw_path = _capture_path(workspace_root)
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
    raw_path = _capture_path(workspace_root)
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
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" in message, message


def test_phase_verdict_quotes_the_api_rejection(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "output_text" in message, message


def test_phase_verdict_omits_transport_failure_when_none_occurred(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _write_clean_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" not in message, message


def test_phase_verdict_omits_transport_failure_when_raw_log_absent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    message = _render_and_capture(tmp_path, monkeypatch)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" not in message, message


def _write_recovered_turn_raw_log(workspace_root: Path) -> None:
    raw_path = _capture_path(workspace_root)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({"type": "turn.failed", "error": {"message": _API_REJECTION}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1}}),
    ]
    raw_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_recovered_turn_failure_is_not_reported_as_the_cause(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _write_recovered_turn_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "agent turn failed at the transport" not in message, message


def test_transport_failure_detail_contains_terminal_escapes(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    raw_path = _capture_path(tmp_path)
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
    raw_path = _capture_path(tmp_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "turn.started"}),
                json.dumps({"type": "turn.failed", "error": {"message": _API_REJECTION}}),
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
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch)

    assert "transcript reports:" in message
    assert f'"{_API_REJECTION}"' in message


def test_a_shared_capture_suppresses_transport_attribution(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    _write_turn_failed_raw_log(tmp_path)

    message = _render_and_capture(tmp_path, monkeypatch, shared_capture=True)

    assert "FAILED (no artifact)" in message, message
    assert "agent turn failed at the transport" not in message, message
