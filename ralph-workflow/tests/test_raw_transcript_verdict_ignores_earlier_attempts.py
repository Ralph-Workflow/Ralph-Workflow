from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke.raw_transcript_attempt import mark_raw_transcript_attempt_start
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline.phase_agent_handler import compute_graded_phase_verdict

if TYPE_CHECKING:
    from pathlib import Path

_CUT_SHORT = b'{"type":"agent_end","messages":[{"role":"system","content":"interrupted\n'
_WHOLE = b'{"type":"message","text":"ok"}\n'


def _config() -> AgentConfig:
    return AgentConfig(cmd="pi", transport=AgentTransport.PI, model="minimax/MiniMax-M3")


def _transcript(workspace_root: Path) -> Path:
    config = _config()
    path = raw_log_path_for(workspace_root, raw_log_unit_id_for(config), model=config.model)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _required() -> RequiredArtifact:
    return RequiredArtifact(
        phase="development",
        artifact_type="development_result",
        artifact_path=".agent/artifacts/development_result.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )


def _detail(workspace_root: Path) -> str:
    _label, _weakest, detail = compute_graded_phase_verdict(
        workspace_root, _required(), None, _config()
    )
    return detail


def test_a_line_cut_short_by_an_earlier_attempt_is_not_blamed_on_this_one(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    transcript.write_bytes(_WHOLE + _CUT_SHORT)

    mark_raw_transcript_attempt_start(tmp_path, _config())
    with transcript.open("ab") as handle:
        handle.write(_WHOLE * 3)

    assert "corrupted" not in _detail(tmp_path)


def test_a_line_cut_short_during_this_attempt_is_still_reported(tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    transcript.write_bytes(_WHOLE)

    mark_raw_transcript_attempt_start(tmp_path, _config())
    with transcript.open("ab") as handle:
        handle.write(_WHOLE + _CUT_SHORT + _WHOLE)

    assert "raw transcript corrupted" in _detail(tmp_path)


def test_a_transcript_rotated_after_the_attempt_began_is_read_from_its_start(
    tmp_path: Path,
) -> None:
    transcript = _transcript(tmp_path)
    transcript.write_bytes(_WHOLE * 40)

    mark_raw_transcript_attempt_start(tmp_path, _config())
    transcript.write_bytes(_CUT_SHORT + _WHOLE)

    assert "raw transcript corrupted" in _detail(tmp_path)
