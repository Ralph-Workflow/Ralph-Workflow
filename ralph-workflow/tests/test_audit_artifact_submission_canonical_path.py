"""Tests for ralph.testing.audit_artifact_submission_canonical_path.

The canonical-path audit enforces that all artifact submission side effects
(receipts, completion sentinels, canonical artifact files, and the lower-level
submit/receipt helpers) route through ``submit_artifact_canonical``. These tests
pin the detection rules and the allow-list behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_artifact_submission_canonical_path import (
    audit,
    audit_file,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path


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
