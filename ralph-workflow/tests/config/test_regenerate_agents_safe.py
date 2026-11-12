from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest
from loguru import logger
from typer.testing import CliRunner

from ralph.cli.main import app
from ralph.config.agents_file_writer import AgentsFileConflictError
from ralph.config.bootstrap import ensure_global_agents_config, regenerate_all

AGENTS_FILENAME = "ralph-workflow-agents.toml"


def _backup_holding_bytes(target: Path, expected: bytes) -> Path | None:
    candidates = [target.with_name(f"{target.name}.bak")]
    resolved = target.resolve()
    resolved_candidate = resolved.with_name(f"{resolved.name}.bak")
    if resolved_candidate not in candidates:
        candidates.append(resolved_candidate)
    for candidate in candidates:
        if candidate.is_file() and candidate.read_bytes() == expected:
            return candidate
    return None


def _capture_loguru_records(level: str = "DEBUG") -> tuple[list[tuple[str, int]], int]:
    records: list[tuple[str, int]] = []

    def sink(message: object) -> None:
        record = getattr(message, "record", None)
        if not isinstance(record, dict):
            return
        text = str(record.get("message", ""))
        number = getattr(record.get("level"), "no", 0)
        records.append((text, number if isinstance(number, int) else 0))

    sink_id = logger.add(sink, level=level)
    return records, sink_id


def test_ensure_global_agents_config_force_creates_backup_with_operator_edits(
    tmp_path: Path,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    custom_content = '[agents.my_custom_agent]\ncmd = "custom-agent"\n'
    agents_file.write_text(custom_content, encoding="utf-8")

    result = ensure_global_agents_config(global_dir, force=True)

    bak_path = global_dir / f"{AGENTS_FILENAME}.bak"
    assert result.action == "regenerated"
    assert result.backup == bak_path
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == custom_content

    assert agents_file.exists()
    target_content = agents_file.read_text(encoding="utf-8")
    assert target_content != custom_content
    data = tomllib.loads(target_content)
    assert "agents" in data


def test_regenerate_all_creates_backup_with_operator_edits(
    tmp_path: Path,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    custom_content = '[agents.special]\ncmd = "special-agent"\n'
    agents_file.write_text(custom_content, encoding="utf-8")

    results = regenerate_all(global_dir=global_dir)

    agents_results = [r for r in results if r.path.name == AGENTS_FILENAME]
    assert len(agents_results) == 1
    assert agents_results[0].action == "regenerated"

    bak_path = global_dir / f"{AGENTS_FILENAME}.bak"
    assert bak_path.exists()
    assert bak_path.read_text(encoding="utf-8") == custom_content

    target_content = agents_file.read_text(encoding="utf-8")
    data = tomllib.loads(target_content)
    assert "agents" in data


def test_ensure_global_agents_config_force_logs_trigger(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")

    with caplog.at_level(logging.INFO):
        ensure_global_agents_config(global_dir, force=True)

    assert "trigger: ralph --regenerate-config" in caplog.text
    assert str(agents_file) in caplog.text


def test_regenerate_all_logs_trigger(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")

    with caplog.at_level(logging.INFO):
        regenerate_all(global_dir=global_dir)

    assert "trigger: ralph --regenerate-config" in caplog.text


def test_ensure_global_agents_config_validates_toml_before_replacing(
    tmp_path: Path,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    initial_content = '[agents.stable]\ncmd = "stable"\n'
    agents_file.write_text(initial_content, encoding="utf-8")

    with (
        patch(
            "ralph.config.agents_bootstrap.write_agents_toml",
            side_effect=ValueError("Invalid TOML: unclosed table"),
        ),
        pytest.raises(ValueError, match="Invalid TOML"),
    ):
        ensure_global_agents_config(global_dir, force=True)

    assert agents_file.read_text(encoding="utf-8") == initial_content
    assert not (global_dir / f"{AGENTS_FILENAME}.bak").exists()


def test_ensure_global_agents_config_commits_without_double_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.custom]\ncmd = "v1"\n', encoding="utf-8")

    committed_paths: list[list[str]] = []
    committed_subjects: list[str] = []

    def fake_classify(_target: Path, _context: Path) -> str:
        return "tracked"

    def fake_commit_deterministic_writes(
        _repo_root: Path,
        *,
        paths: list[str],
        pre_contents: dict[str, str | None],
        subject: str,
        create_commit_fn: object = None,
        stage_fn: object = None,
    ) -> object:
        from ralph.git.scoped_auto_commit import ScopedCommitResult, ScopedCommitStatus

        has_changes = any(
            pre_contents.get(p)
            != ((_repo_root / p).read_text(encoding="utf-8") if (_repo_root / p).exists() else None)
            for p in paths
        )
        if not has_changes:
            return ScopedCommitResult(ScopedCommitStatus.SKIPPED, sha=None, error=None)

        committed_paths.append(paths)
        committed_subjects.append(subject)
        return ScopedCommitResult(ScopedCommitStatus.CREATED, sha="12345678", error=None)

    def fake_capture(_repo_root: Path, paths: list[str]) -> dict[str, str | None]:
        return {
            p: (_repo_root / p).read_text(encoding="utf-8") if (_repo_root / p).exists() else None
            for p in paths
        }

    monkeypatch.setattr("ralph.config.agents_bootstrap.classify_target_for_commit", fake_classify)
    monkeypatch.setattr(
        "ralph.config.agents_bootstrap.commit_deterministic_writes",
        fake_commit_deterministic_writes,
    )
    monkeypatch.setattr("ralph.git.scoped_auto_commit.capture_pre_write_contents", fake_capture)

    class FakeRepo:
        def __init__(self, _anchor: Path, search_parent_directories: bool = True) -> None:
            self.working_dir = str(tmp_path)

    monkeypatch.setattr("ralph.config.agents_bootstrap.Repo", FakeRepo)

    result = ensure_global_agents_config(global_dir, force=True)
    assert result.action == "regenerated"
    assert len(committed_subjects) == 1
    assert committed_subjects[0] == f"chore(config): update {AGENTS_FILENAME}"
    assert committed_paths[0] == [agents_file.relative_to(tmp_path).as_posix()]
    assert not any(p.endswith(".bak") for p in committed_paths[0])

    result2 = ensure_global_agents_config(global_dir, force=True)
    assert result2.action == "skipped"
    assert result2.backup is None
    assert len(committed_subjects) == 1


def test_regenerate_agents_with_invalid_utf8_keeps_original_bytes_in_backup(
    tmp_path: Path,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    original_bytes = b'[agents.claude]\ncmd = "claude"\n# broken byte: \xff\xfe\n'
    agents_file.write_bytes(original_bytes)

    results = regenerate_all(global_dir=global_dir)

    agents_results = [r for r in results if r.path.name == AGENTS_FILENAME]
    assert len(agents_results) == 1
    assert agents_results[0].action == "regenerated"

    rewritten = agents_file.read_text(encoding="utf-8")
    assert "agents" in tomllib.loads(rewritten)

    assert _backup_holding_bytes(agents_file, original_bytes) is not None


def test_regenerate_config_command_on_invalid_utf8_agents_file_yields_parseable_file_and_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "xdg-config"
    config_home.mkdir(parents=True)
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True)
    agents_file = config_home / AGENTS_FILENAME
    original_bytes = b'[agents.claude]\ncmd = "claude"\n# broken byte: \xff\xfe\n'
    agents_file.write_bytes(original_bytes)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(workspace_dir)

    result = CliRunner().invoke(app, ["--regenerate-config"])

    assert result.exit_code == 0, result.output
    rewritten = agents_file.read_text(encoding="utf-8")
    assert "agents" in tomllib.loads(rewritten)
    assert _backup_holding_bytes(agents_file, original_bytes) is not None


def test_regenerate_agents_emits_trigger_line_to_operator_visible_loguru_sink(
    tmp_path: Path,
) -> None:
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    agents_file.write_text('[agents.claude]\ncmd = "claude-old"\n', encoding="utf-8")
    bak_path = global_dir / f"{AGENTS_FILENAME}.bak"

    records, sink_id = _capture_loguru_records()
    try:
        regenerate_all(global_dir=global_dir)
    finally:
        logger.remove(sink_id)

    matching = [
        (text, level_no)
        for text, level_no in records
        if str(agents_file) in text
        and "ralph --regenerate-config" in text
        and (str(bak_path) in text or bak_path.name in text)
    ]
    assert matching, (
        "no operator-visible trigger line for the regenerate path; captured: "
        f"{[text for text, _ in records]}"
    )
    assert max(level_no for _, level_no in matching) >= 20


def test_ensure_global_agents_config_conflict_propagates_without_stale_backup_or_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit regeneration propagates AgentsFileConflictError without overwrite or stale backup."""
    global_dir = tmp_path / "config"
    global_dir.mkdir(parents=True)
    agents_file = global_dir / AGENTS_FILENAME
    custom_content = '[agents.my_custom_agent]\ncmd = "custom-agent"\n'
    agents_file.write_text(custom_content, encoding="utf-8")
    operator_interleaved = '[agents.interleaved]\ncmd = "preserve-interleaved"\n'

    real_replace = Path.replace

    def interleaving_replace(self: Path, target: object, *args: object, **kwargs: object) -> Path:
        result = real_replace(self, target, *args, **kwargs)
        if str(target).endswith(".bak"):
            agents_file.write_text(operator_interleaved, encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "replace", interleaving_replace)

    with pytest.raises(AgentsFileConflictError):
        ensure_global_agents_config(global_dir, force=True)

    # Operator interleaved edit is preserved on disk
    assert agents_file.read_text(encoding="utf-8") == operator_interleaved

    # Temp files cleaned up
    leftovers = [
        f.name
        for f in global_dir.iterdir()
        if f != agents_file and not f.name.endswith(".bak") and not f.name.endswith(".lock")
    ]
    assert leftovers == []
