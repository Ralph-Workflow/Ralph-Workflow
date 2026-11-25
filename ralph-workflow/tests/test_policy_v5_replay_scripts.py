from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.doc_roots import PACKAGE_ROOT, REPOSITORY_ROOT

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(5)]

RALPH_TEST_CHANGE_REASONS = {
    "test_run_script_pins_exact_harness_command": (
        "replay-plan section 4 holds the harness constant across arms, so the run script "
        "must carry the exact pinned harness command as one contiguous string and a drift "
        "in any token fails the pin"
    ),
    "test_run_script_pins_model_id_and_per_run_timeout": (
        "the pilot meta.json records model claude-opus-5-5 and agent_exit 124 from the "
        "harness timeout 720, so the run script must pin that model id and that per-run "
        "timeout"
    ),
    "test_run_script_pins_the_canonical_ledger_path": (
        "the canonical pilot ledger lives at tmp/policy-v6-replay-runs/ledger.jsonl under "
        "the package root, so the run script must resolve and append to that path"
    ),
    "test_run_script_runs_the_arm_under_setsid": (
        "replay-plan section 6.5 caps each run at 30 minutes with a watchdog that "
        "terminates the agent process tree, so the run script must launch the arm in its "
        "own session under setsid"
    ),
    "test_run_script_tags_arm_ids_without_pack_identity": (
        "the run side is treatment-blind, so the run script tags arm ids only and never "
        "carries pack identity that would unblind the reviewer"
    ),
    "test_run_script_writes_section6_failure_record_fields": (
        "replay-plan section 6 counts a crashed or timed-out run as a failure on the pack, "
        "so the ledger row written on crash or timeout must carry the run_id, arm, "
        "category, outcome, wall_seconds, invalid and scores_path fields"
    ),
    "test_run_script_records_a_failed_row_on_a_simulated_timeout": (
        "the pilot arm R3 timed out at 720 s and was recorded as outcome failed, so the "
        "run script must append that row shape when the harness times out"
    ),
    "test_run_script_records_a_failed_row_on_a_simulated_crash": (
        "replay-plan section 6 counts a crashed run as a failure on the pack rather than a "
        "sample to replace, so the run script must append outcome failed on a non-zero "
        "harness exit"
    ),
    "test_run_script_refuses_a_mismatched_model_id": (
        "replay-plan section 4 requires the runner to refuse to start when the harness "
        "model differs from the pinned id, and a refused run must leave the ledger "
        "untouched"
    ),
    "test_run_script_records_a_valid_row_with_the_cold_scoring_scores_path": (
        "the pilot valid rows carry the shared scores.json path, so a completed arm whose "
        "cold scoring pass wrote the scores must append outcome valid with that path"
    ),
    "test_freeze_script_pins_the_run_id_and_version_metadata": (
        "freeze --verify checks the run id and version pins of both plan copies, so the "
        "freeze script must carry the pinned v4 baseline sha, the pinned v5 treatment sha "
        "and the pinned run id artifact root as literals"
    ),
    "test_freeze_verify_accepts_matching_plan_copies": (
        "freeze --verify is the drift gate for the paired plan copies, so it must exit 0 "
        "while both copies are byte-identical and carry the pinned run id and version "
        "metadata"
    ),
    "test_freeze_verify_rejects_a_corrupted_plan_copy": (
        "freeze --verify must fail closed on drift, so a deliberately corrupted mirror "
        "copy must produce a non-zero exit"
    ),
    "test_freeze_resyncs_a_drifted_mirror_copy": (
        "the freeze operation must copy the canonical plan over the mirror, so a drifted "
        "mirror must become byte-identical again after a freeze run"
    ),
    "test_plan_d0_creates_the_snapshots_ledger_dir": (
        "the section D.0 reference script appends to tmp/policy-v5-replay-snapshots/"
        "ledger.jsonl without creating that directory first, so both plan copies must "
        "create the directory before the append"
    ),
    "test_scripts_parse_under_bash_n": (
        "both replay scripts are bash entry points, so they must parse under bash -n"
    ),
    "test_scripts_are_executable": (
        "the replay scripts are invoked as executables, so the shipped files must keep "
        "their executable mode bit"
    ),
}

_RUN_SCRIPT = PACKAGE_ROOT / "scripts" / "policy_v5_replay_run.sh"
_FREEZE_SCRIPT = PACKAGE_ROOT / "scripts" / "policy_v5_replay_freeze.sh"

_PLAN_FILE_NAME = "policy-v5-paired-replay-plan.md"
_CANONICAL_PLAN_RELATIVE_PATH = f"ralph-workflow/docs/agents/{_PLAN_FILE_NAME}"
_MIRROR_PLAN_RELATIVE_PATH = f"ralph-workflow/docs/plans/{_PLAN_FILE_NAME}"
_PLAN_RELATIVE_PATHS = (
    _CANONICAL_PLAN_RELATIVE_PATH,
    _MIRROR_PLAN_RELATIVE_PATH,
)

_PINNED_HARNESS_COMMAND = (
    "timeout 720 claude -p --print --output-format=stream-json "
    "--model claude-opus-5-5 --permission-mode auto"
)
_PINNED_MODEL_ID = "claude-opus-5-5"
_RUN_TIMEOUT_SECONDS = 720
_LEDGER_RELATIVE_PATH = "tmp/policy-v6-replay-runs/ledger.jsonl"
_LEDGER_FIELDS = (
    "run_id",
    "arm",
    "category",
    "outcome",
    "wall_seconds",
    "invalid",
    "scores_path",
)
_PINNED_V4_SHA = "c67e7536fe8b97831c72d3e5d87644c26b4b2342"
_PINNED_V5_SHA = "f760933b2fae095ecdb8bf9f06ae8125a5dd9e90"
_PINNED_RUN_ID_ROOT = "tmp/policy-v5-replay-runs/<run_id>/"
_SNAPSHOTS_LEDGER_DIR = 'mkdir -p "tmp/policy-v5-replay-snapshots"'
_SNAPSHOTS_LEDGER_APPEND = '>> "tmp/policy-v5-replay-snapshots/ledger.jsonl"'

_RUN_ID = "policy-v6-replay-S2-A"
_ARM_ID = "R3"
_CATEGORY = "S2"
_SCORES_PATH = "ralph-workflow/tmp/policy-v6-replay-runs/scores.json"
_SYSTEM_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_GNU_TOOLS_THE_RUN_SCRIPT_NEEDS = ("timeout", "setsid")
_COMMAND_TIMEOUT_SECONDS = 30

_FAKE_CLAUDE_SOURCE = """#!/usr/bin/env bash
prompt="${!#}"
if [[ "${prompt}" == *"SCORING_PASS"* ]]; then
  scores_path="${prompt#*write the arm scores to }"
  scores_path="${scores_path%% in the scores.json schema.*}"
  printf '{"S2-A": {"check first": {"score": 2, "citation": "fake cold scoring pass"}}}\\n' > "${scores_path}"
  exit 0
fi
exit "${FAKE_CLAUDE_EXIT}"
"""


def _read_text(path: Path) -> str:
    assert path.is_file(), f"expected shipped file at {path}"
    return path.read_text(encoding="utf-8")


def _plan_text() -> str:
    return _read_text(REPOSITORY_ROOT / _CANONICAL_PLAN_RELATIVE_PATH)


def _d0_block(relative_path: str) -> str:
    text = _read_text(REPOSITORY_ROOT / relative_path)
    start = text.index("### D.0")
    end = text.index("### D.1")
    return text[start:end]


def _base_env() -> dict[str, str]:
    return {"PATH": _SYSTEM_PATH, "HOME": "/tmp", "LANG": "C.UTF-8"}


def _write_fake_claude(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(_FAKE_CLAUDE_SOURCE, encoding="utf-8")
    fake_claude.chmod(0o755)
    return fake_claude


def _decode_row(line: str) -> dict[str, object]:
    decoded: object = json.loads(line)
    assert isinstance(decoded, dict), f"ledger row is not a JSON object: {line}"
    row: dict[str, object] = {}
    for key, value in decoded.items():
        assert isinstance(key, str), f"ledger row key is not a string: {line}"
        row[key] = value
    return row


def _ledger_rows(package_root: Path) -> list[dict[str, object]]:
    ledger_path = package_root / _LEDGER_RELATIVE_PATH
    if not ledger_path.exists():
        return []
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    return [_decode_row(line) for line in lines if line.strip()]


def _run_script(
    tmp_path: Path,
    *,
    claude_exit: int,
    model_id: str = _PINNED_MODEL_ID,
    scoring_prompt: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    package_root = tmp_path / "ralph-workflow"
    scripts_dir = package_root / "scripts"
    scripts_dir.mkdir(parents=True)
    run_script = scripts_dir / _RUN_SCRIPT.name
    shutil.copy2(_RUN_SCRIPT, run_script)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("rename the service boundary\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    _write_fake_claude(bin_dir)
    argv = [
        "bash",
        str(run_script),
        "run",
        _RUN_ID,
        _ARM_ID,
        _CATEGORY,
        str(snapshot_dir),
        str(prompt_file),
    ]
    if scoring_prompt is not None:
        scoring_file = tmp_path / "scoring-prompt.md"
        scoring_file.write_text(scoring_prompt, encoding="utf-8")
        argv.append(str(scoring_file))
    env = _base_env()
    env["PATH"] = f"{bin_dir}:{_SYSTEM_PATH}"
    env["RALPH_AGENT_MODEL"] = model_id
    env["FAKE_CLAUDE_EXIT"] = str(claude_exit)
    result = subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )
    return result, package_root


def _isolated_plan_package(tmp_path: Path, *, corrupt_mirror: bool) -> Path:
    package_root = tmp_path / "ralph-workflow"
    (package_root / "scripts").mkdir(parents=True)
    (package_root / "docs" / "agents").mkdir(parents=True)
    (package_root / "docs" / "plans").mkdir(parents=True)
    shutil.copy2(_FREEZE_SCRIPT, package_root / "scripts" / _FREEZE_SCRIPT.name)
    plan_text = _plan_text()
    (package_root / "docs" / "agents" / _PLAN_FILE_NAME).write_text(plan_text, encoding="utf-8")
    mirror_text = f"{plan_text}\ndrift-probe\n" if corrupt_mirror else plan_text
    (package_root / "docs" / "plans" / _PLAN_FILE_NAME).write_text(mirror_text, encoding="utf-8")
    return package_root


def _run_freeze(package_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(package_root / "scripts" / _FREEZE_SCRIPT.name), *arguments],
        env=_base_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )


def test_run_script_pins_exact_harness_command() -> None:
    text = _read_text(_RUN_SCRIPT)
    assert _PINNED_HARNESS_COMMAND in text, (
        f"{_RUN_SCRIPT} must pin the exact harness command {_PINNED_HARNESS_COMMAND!r}"
    )


def test_run_script_pins_model_id_and_per_run_timeout() -> None:
    text = _read_text(_RUN_SCRIPT)
    assert _PINNED_MODEL_ID in text, f"{_RUN_SCRIPT} must pin the model id"
    assert str(_RUN_TIMEOUT_SECONDS) in text, f"{_RUN_SCRIPT} must pin the per-run timeout"


def test_run_script_pins_the_canonical_ledger_path() -> None:
    text = _read_text(_RUN_SCRIPT)
    assert _LEDGER_RELATIVE_PATH in text, f"{_RUN_SCRIPT} must pin the canonical ledger path"
    assert 'ledger_path="${package_root}/${LEDGER_RELATIVE_PATH}"' in text, (
        f"{_RUN_SCRIPT} must resolve the ledger under the package root"
    )


def test_run_script_runs_the_arm_under_setsid() -> None:
    text = _read_text(_RUN_SCRIPT)
    assert "setsid" in text, f"{_RUN_SCRIPT} must launch the arm in its own session"


def test_run_script_tags_arm_ids_without_pack_identity() -> None:
    text = _read_text(_RUN_SCRIPT)
    assert "<arm_id>" in text, f"{_RUN_SCRIPT} usage must name the arm_id argument"
    for forbidden in ("<pack>", "<treatment>", "<pack_id>", "pack_sha256"):
        assert forbidden not in text, f"{_RUN_SCRIPT} must not carry pack identity: {forbidden!r}"


def test_run_script_writes_section6_failure_record_fields() -> None:
    text = _read_text(_RUN_SCRIPT)
    for field in _LEDGER_FIELDS:
        assert f'"{field}"' in text, (
            f"{_RUN_SCRIPT} must write the section 6 failure-record field {field!r}"
        )
    assert '"failed"' in text, f"{_RUN_SCRIPT} must record the failed outcome"


def test_run_script_records_a_failed_row_on_a_simulated_timeout(tmp_path: Path) -> None:
    result, package_root = _run_script(tmp_path, claude_exit=124)
    assert result.returncode != 0, (
        f"a timed-out arm must fail the run; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    rows = _ledger_rows(package_root)
    assert len(rows) == 1, f"expected exactly one ledger row, found {len(rows)}"
    row = rows[0]
    assert list(row) == list(_LEDGER_FIELDS), f"unexpected ledger row schema: {row}"
    assert row["run_id"] == _RUN_ID
    assert row["arm"] == _ARM_ID
    assert row["category"] == _CATEGORY
    assert row["outcome"] == "failed"
    assert isinstance(row["wall_seconds"], int)
    assert row["invalid"] is None
    assert row["scores_path"] is None


def test_run_script_records_a_failed_row_on_a_simulated_crash(tmp_path: Path) -> None:
    result, package_root = _run_script(tmp_path, claude_exit=1)
    assert result.returncode != 0, (
        f"a crashed arm must fail the run; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    rows = _ledger_rows(package_root)
    assert len(rows) == 1, f"expected exactly one ledger row, found {len(rows)}"
    row = rows[0]
    assert list(row) == list(_LEDGER_FIELDS), f"unexpected ledger row schema: {row}"
    assert row["outcome"] == "failed"
    assert row["invalid"] is None
    assert row["scores_path"] is None


def test_run_script_refuses_a_mismatched_model_id(tmp_path: Path) -> None:
    result, package_root = _run_script(tmp_path, claude_exit=0, model_id="not-the-pinned-model")
    assert result.returncode != 0, (
        f"the run script must refuse a mismatched model id; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert _ledger_rows(package_root) == [], "a refused run must not append a ledger row"


@pytest.mark.skipif(
    any(shutil.which(tool, path=_SYSTEM_PATH) is None for tool in _GNU_TOOLS_THE_RUN_SCRIPT_NEEDS),
    reason="the replay run script needs GNU timeout and setsid on the system PATH",
)
def test_run_script_records_a_valid_row_with_the_cold_scoring_scores_path(
    tmp_path: Path,
) -> None:
    result, package_root = _run_script(
        tmp_path,
        claude_exit=0,
        scoring_prompt="Score the capture against the rubric.\n",
    )
    assert result.returncode == 0, (
        f"a completed arm must exit 0; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    rows = _ledger_rows(package_root)
    assert len(rows) == 1, f"expected exactly one ledger row, found {len(rows)}"
    row = rows[0]
    assert list(row) == list(_LEDGER_FIELDS), f"unexpected ledger row schema: {row}"
    assert row["outcome"] == "valid"
    assert row["invalid"] is None
    assert row["scores_path"] == _SCORES_PATH
    assert (package_root / "tmp/policy-v6-replay-runs/scores.json").is_file()


def test_freeze_script_pins_the_run_id_and_version_metadata() -> None:
    text = _read_text(_FREEZE_SCRIPT)
    for pinned in (_PINNED_V4_SHA, _PINNED_V5_SHA, _PINNED_RUN_ID_ROOT):
        assert pinned in text, f"{_FREEZE_SCRIPT} must pin {pinned!r}"


def test_freeze_verify_accepts_matching_plan_copies(tmp_path: Path) -> None:
    package_root = _isolated_plan_package(tmp_path, corrupt_mirror=False)
    result = _run_freeze(package_root, "--verify")
    assert result.returncode == 0, (
        f"freeze --verify must exit 0 on matching plan copies; stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


def test_freeze_verify_rejects_a_corrupted_plan_copy(tmp_path: Path) -> None:
    package_root = _isolated_plan_package(tmp_path, corrupt_mirror=True)
    result = _run_freeze(package_root, "--verify")
    assert result.returncode != 0, "freeze --verify must fail closed on a corrupted copy"
    assert "differ" in result.stderr, f"unexpected stderr: {result.stderr!r}"


def test_freeze_resyncs_a_drifted_mirror_copy(tmp_path: Path) -> None:
    package_root = _isolated_plan_package(tmp_path, corrupt_mirror=True)
    result = _run_freeze(package_root)
    assert result.returncode == 0, (
        f"freeze must resync the mirror; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    canonical = package_root / "docs" / "agents" / _PLAN_FILE_NAME
    mirror = package_root / "docs" / "plans" / _PLAN_FILE_NAME
    assert mirror.read_bytes() == canonical.read_bytes()


@pytest.mark.parametrize("relative_path", _PLAN_RELATIVE_PATHS)
def test_plan_d0_creates_the_snapshots_ledger_dir(relative_path: str) -> None:
    block = _d0_block(relative_path)
    assert _SNAPSHOTS_LEDGER_DIR in block, (
        f"{relative_path} section D.0 must create the snapshots ledger directory"
    )
    assert block.index(_SNAPSHOTS_LEDGER_DIR) < block.index(_SNAPSHOTS_LEDGER_APPEND), (
        f"{relative_path} section D.0 must create the ledger directory before the append"
    )


@pytest.mark.parametrize("script_path", (_RUN_SCRIPT, _FREEZE_SCRIPT))
def test_scripts_parse_under_bash_n(script_path: Path) -> None:
    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"{script_path} must parse under bash -n: {result.stderr!r}"


@pytest.mark.parametrize("script_path", (_RUN_SCRIPT, _FREEZE_SCRIPT))
def test_scripts_are_executable(script_path: Path) -> None:
    assert os.access(script_path, os.X_OK), f"{script_path} must keep its executable mode bit"
