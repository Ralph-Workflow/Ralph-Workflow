"""Tests for ralph.testing.audit_artifact_submission_canonical_path.

The canonical-path audit enforces that all artifact submission side effects
(receipts, completion sentinels, canonical artifact files, and the lower-level
submit/receipt helpers) route through ``submit_artifact_canonical``. These tests
pin the detection rules and the allow-list behavior.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ralph.mcp.tools.artifact import _KNOWN_ARTIFACT_TYPES
from ralph.testing.audit_artifact_submission_canonical_path import (
    _CANONICAL_TYPES,
    _assert_invariants,
    audit,
    audit_file,
    main,
)


def _write(tmp_path: Path, rel_path: str, src: str) -> Path:
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(src, encoding="utf-8")
    return file_path


def test_direct_receipt_write_text_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\nPath('.agent/receipts/x.json').write_text('{}')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_direct_receipt_write_bytes_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\n"
        "Path('.agent/receipts/x/commit_message.json').write_bytes(b'x')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_direct_completion_sentinel_open_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "with open('.agent/completion_seen_run.json', 'w') as fh:\n    fh.write('x')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "sentinel_write"


def test_direct_canonical_artifact_write_text_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\nPath('.agent/artifacts/plan.json').write_text('{}')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "canonical_artifact_write"


def test_direct_fallback_tmp_write_text_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\nPath('.agent/tmp/commit_message.json').write_text('{}')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "fallback_tmp_write"


def test_os_rename_into_sentinel_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import os\n"
        "os.rename('src.txt', '.agent/completion_seen_run-1.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "sentinel_write"


def test_shutil_copy_with_keyword_dst_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\n"
        "shutil.copy('src.txt', dst='.agent/receipts/commit_message.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_aliased_shutil_copy_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil as s\n"
        "s.copy('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_shutil_copy_outside_protected_path_is_not_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\n"
        "shutil.copy('src.txt', '/tmp/somewhere/safe')\n",
    )
    findings = audit_file(f, "mod.py")
    assert not findings


def test_store_submit_artifact_call_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "store.submit_artifact('plan', content={})\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "store_submit_artifact"


def test_direct_import_submit_artifact_call_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from ralph.mcp.artifacts.store import submit_artifact\n"
        "submit_artifact('plan', content={})\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "store_submit_artifact"


def test_write_artifact_receipt_call_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "write_artifact_receipt(workspace_root, run_id, artifact_type)\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_helper"


def test_delete_artifact_receipt_call_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "delete_artifact_receipt(workspace_root, run_id, artifact_type)\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_helper"


def test_allowed_path_patterns_are_ignored(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\nPath('.agent/artifacts/unknown.json').write_text('{}')\n",
    )
    findings = audit_file(f, "mod.py")
    assert not findings


def test_allowed_function_names_are_ignored(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "submit_artifact_canonical('plan', content={})\n",
    )
    findings = audit_file(f, "mod.py")
    assert not findings


def test_canonical_block_marker_allows_writes(tmp_path: Path) -> None:
    src = """\
# === BEGIN CANONICAL SUBMIT OPS ===
from pathlib import Path
Path('.agent/receipts/x.json').write_text('{}')
write_artifact_receipt(workspace_root, run_id, artifact_type)
# === END CANONICAL SUBMIT OPS ===
"""
    f = _write(tmp_path, "mod.py", src)
    findings = audit_file(f, "mod.py")
    assert not findings


def test_audit_skips_tests_directory(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "tests/evil.py",
        "from pathlib import Path\nPath('.agent/receipts/x.json').write_text('{}')\n",
    )
    findings = audit(codebase_root=tmp_path)
    assert not findings


def test_audit_skips_allowlisted_files(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ralph/mcp/artifacts/commit_message.py",
        "from pathlib import Path\nPath('.agent/receipts/x.json').write_text('{}')\n",
    )
    findings = audit(codebase_root=tmp_path)
    assert not findings


def test_audit_discovers_bypasses_across_package(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ralph/evil.py",
        "from pathlib import Path\nPath('.agent/receipts/x.json').write_text('{}')\n",
    )
    _write(
        tmp_path,
        "ralph/good.py",
        "x = 1\n",
    )
    findings = audit(codebase_root=tmp_path)
    assert len(findings) == 1
    assert findings[0].file_path == "ralph/evil.py"


def test_path_composition_with_div_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "ralph/mod.py",
        "from pathlib import Path\nrun_id = 'run-1'\nartifact_type = 'commit_message'\n"
        "(Path('.agent') / 'receipts' / run_id / f'{artifact_type}.json').write_text('{}')\n",
    )
    findings = audit_file(f, "ralph/mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_pre_marker_bypass_write_is_flagged(tmp_path: Path) -> None:
    src = """\
from pathlib import Path
Path('.agent/receipts/x.json').write_text('{}')
# === BEGIN CANONICAL SUBMIT OPS ===
x = 1
# === END CANONICAL SUBMIT OPS ===
"""
    f = _write(tmp_path, "ralph/mod.py", src)
    findings = audit_file(f, "ralph/mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    _write(tmp_path, "ralph/good.py", "x = 1\n")
    assert main([str(tmp_path)]) == 0


def test_main_returns_nonzero_when_bypass_exists(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "ralph/evil.py",
        "from pathlib import Path\nPath('.agent/receipts/x.json').write_text('{}')\n",
    )
    assert main([str(tmp_path)]) == 1


# =============================================================================
# Step 5 — Import-time invariant enforcement
# =============================================================================


def test_audit_invariants_fire_when_canonical_types_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.testing.audit_artifact_submission_canonical_path._CANONICAL_TYPES",
        frozenset(),
    )
    with pytest.raises(RuntimeError, match="_CANONICAL_TYPES must not be empty"):
        _assert_invariants()


def test_audit_invariants_fire_when_required_canonical_type_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = frozenset(t for t in _CANONICAL_TYPES if t != "commit_message")
    monkeypatch.setattr(
        "ralph.testing.audit_artifact_submission_canonical_path._CANONICAL_TYPES",
        missing,
    )
    with pytest.raises(
        RuntimeError, match="_CANONICAL_TYPES must contain 'commit_message'"
    ):
        _assert_invariants()


def test_audit_invariants_fire_when_file_allowlist_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.testing.audit_artifact_submission_canonical_path._FILE_ALLOWLIST",
        frozenset(),
    )
    with pytest.raises(RuntimeError, match="_FILE_ALLOWLIST must not be empty"):
        _assert_invariants()


def test_audit_invariants_fire_when_allowlist_file_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake_root = tmp_path / "nonexistent"
    monkeypatch.setattr(
        "ralph.testing.audit_artifact_submission_canonical_path._AUDIT_MODULE_ROOT",
        fake_root,
    )
    with pytest.raises(RuntimeError, match="_FILE_ALLOWLIST entry does not exist"):
        _assert_invariants()


def test_audit_invariants_fire_when_marker_block_start_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralph.testing.audit_artifact_submission_canonical_path._CANONICAL_BLOCK_START",
        "",
    )
    with pytest.raises(
        RuntimeError, match="_CANONICAL_BLOCK_START must not be empty"
    ):
        _assert_invariants()


def test_audit_invariants_fire_under_python_minus_o() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            (
                "from ralph.testing.audit_artifact_submission_canonical_path"
                " import _CANONICAL_TYPES; print(len(_CANONICAL_TYPES))"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(Path(__file__).resolve().parent.parent),
        check=False,
    )
    assert result.returncode == 0
    assert int(result.stdout.strip()) >= 1


def test_canonical_types_equals_known_artifact_types() -> None:
    assert _CANONICAL_TYPES == _KNOWN_ARTIFACT_TYPES


# =============================================================================
# Step 7 — shutil, os.rename, Path.replace bypass detection
# =============================================================================


def test_shutil_copy_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\nshutil.copy('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_shutil_move_into_artifacts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\n"
        "shutil.move('src.txt', '.agent/artifacts/plan.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "canonical_artifact_write"


def test_shutil_copyfile_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\n"
        "shutil.copyfile('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_shutil_copytree_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\n"
        "shutil.copytree('src', '.agent/receipts/x')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_os_rename_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import os\nos.rename('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_os_replace_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import os\nos.replace('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_path_replace_into_artifacts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\n"
        "Path('src.txt').replace('.agent/artifacts/plan.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "canonical_artifact_write"


def test_variable_segment_path_composition_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "from pathlib import Path\n"
        "var = 'unknown_type'\n"
        "(Path('.agent/tmp/') / var / 'plan.json').write_text('{}')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "fallback_tmp_write"
