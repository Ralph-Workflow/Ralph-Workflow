"""Tests for ralph.testing.audit_artifact_submission_canonical_path.

The canonical-path audit enforces that all artifact submission side effects
(receipts, completion sentinels, canonical artifact files, and the lower-level
submit/receipt helpers) route through ``submit_artifact_canonical``. These tests
pin the detection rules and the allow-list behavior.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path

from ralph.mcp.tools.artifact import _KNOWN_ARTIFACT_TYPES
from ralph.testing.audit_artifact_submission_canonical_path import (
    _CANONICAL_TYPES,
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
        "import os\nos.rename('src.txt', '.agent/completion_seen_run-1.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "sentinel_write"


def test_shutil_copy_with_keyword_dst_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\nshutil.copy('src.txt', dst='.agent/receipts/commit_message.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_aliased_shutil_copy_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil as s\ns.copy('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_shutil_copy_outside_protected_path_is_not_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\nshutil.copy('src.txt', '/tmp/somewhere/safe')\n",
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


def _get_audit_module_path() -> str:
    """Return the absolute path to ralph/testing/audit_artifact_submission_canonical_path.py."""
    test_dir = Path(__file__).parent
    return str(
        test_dir.parent / "ralph" / "testing" / "audit_artifact_submission_canonical_path.py"
    )


def _run_patched_audit_import(
    patch_pattern: str, patch_replacement: str, *, minus_o: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches the audit module and imports it.

    Creates a temporary copy of the audit module with the given pattern
    replaced, then tries to import it. Returns the subprocess result.
    """
    audit_path = _get_audit_module_path()
    repo_root = str(Path(audit_path).parent.parent.parent)
    original = Path(audit_path).read_text(encoding="utf-8")

    # Also patch _AUDIT_MODULE_ROOT so the patched module finds files correctly
    patched = original.replace(
        "_AUDIT_MODULE_ROOT = Path(__file__).parent.parent.parent",
        f"_AUDIT_MODULE_ROOT = Path({repo_root!r})",
    )

    # Apply the user-provided patch
    patched = patched.replace(patch_pattern, patch_replacement)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="audit_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        # Create a runner script that imports the patched audit module
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
            f"import importlib.util\n"
            f"spec = importlib.util.spec_from_file_location(\n"
            f"    'ralph.testing.audit_artifact_submission_canonical_path',\n"
            f"    {tmp_path!r})\n"
            f"mod = importlib.util.module_from_spec(spec)\n"
            f"spec.loader.exec_module(mod)\n"
            "print('OK')\n"
        )

        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", runner])

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


# --- Positive: clean import works ---


def test_audit_invariants_import_clean_via_subprocess() -> None:
    """Importing audit module with correct constants should succeed."""
    result = _run_patched_audit_import(
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
    )
    assert result.returncode == 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_audit_invariants_import_clean_under_minus_o() -> None:
    """Importing audit module under -O with correct constants should succeed."""
    result = _run_patched_audit_import(
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
        minus_o=True,
    )
    assert result.returncode == 0, (
        f"rc={result.returncode} stdout={result.stdout} stderr={result.stderr}"
    )
    assert "OK" in result.stdout


# --- Negative: invariant violations ---


def test_audit_invariants_fire_when_canonical_types_is_empty() -> None:
    """_CANONICAL_TYPES = frozenset() should raise RuntimeError at import time."""
    result = _run_patched_audit_import(
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
        "_CANONICAL_TYPES: frozenset[str] = frozenset()",
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_CANONICAL_TYPES must not be empty" in result.stderr


def test_audit_invariants_fire_when_required_canonical_type_missing() -> None:
    """Missing 'commit_message' from _CANONICAL_TYPES should raise RuntimeError at import time."""
    # Replace the _CANONICAL_TYPES assignment with one missing 'commit_message'
    result = _run_patched_audit_import(
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
        "_CANONICAL_TYPES: frozenset[str] = frozenset({'plan', 'development_result'})",
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_CANONICAL_TYPES must contain 'commit_message'" in result.stderr


def test_audit_invariants_fire_when_file_allowlist_is_empty() -> None:
    """_FILE_ALLOWLIST = frozenset() should raise RuntimeError at import time."""
    result = _run_patched_audit_import(
        (
            "_FILE_ALLOWLIST: frozenset[str] = frozenset(\n"
            "    {\n"
            '        "ralph/mcp/artifacts/canonical_submit.py",\n'
            '        "ralph/mcp/artifacts/commit_message.py",\n'
            '        "ralph/mcp/artifacts/smoke_test_result.py",\n'
            "    }\n"
            ")\n"
            "\n"
        ),
        "_FILE_ALLOWLIST: frozenset[str] = frozenset()\n\n",
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_FILE_ALLOWLIST must not be empty" in result.stderr


def test_audit_invariants_fire_when_allowlist_file_missing() -> None:
    """Non-existent _FILE_ALLOWLIST entry should raise RuntimeError at import time."""
    result = _run_patched_audit_import(
        '"ralph/mcp/artifacts/canonical_submit.py",',
        '"ralph/mcp/artifacts/nonexistent.py",',
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_FILE_ALLOWLIST entry does not exist" in result.stderr


def test_audit_invariants_fire_when_marker_block_start_is_empty() -> None:
    """Empty _CANONICAL_BLOCK_START should raise RuntimeError at import time."""
    result = _run_patched_audit_import(
        '_CANONICAL_BLOCK_START = "# === BEGIN CANONICAL SUBMIT OPS ==="',
        '_CANONICAL_BLOCK_START = ""',
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_CANONICAL_BLOCK_START must not be empty" in result.stderr


def test_audit_invariants_survive_minus_o() -> None:
    """Invariant violations must still raise RuntimeError under python -O."""
    result = _run_patched_audit_import(
        "_CANONICAL_TYPES: frozenset[str] = _KNOWN_ARTIFACT_TYPES",
        "_CANONICAL_TYPES: frozenset[str] = frozenset()",
        minus_o=True,
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_CANONICAL_TYPES must not be empty" in result.stderr


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
        "import shutil\nshutil.move('src.txt', '.agent/artifacts/plan.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "canonical_artifact_write"


def test_shutil_copyfile_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\nshutil.copyfile('src.txt', '.agent/receipts/x.json')\n",
    )
    findings = audit_file(f, "mod.py")
    assert len(findings) == 1
    assert findings[0].category == "receipt_write"


def test_shutil_copytree_into_receipts_is_flagged(tmp_path: Path) -> None:
    f = _write(
        tmp_path,
        "mod.py",
        "import shutil\nshutil.copytree('src', '.agent/receipts/x')\n",
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
        "from pathlib import Path\nPath('src.txt').replace('.agent/artifacts/plan.json')\n",
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
