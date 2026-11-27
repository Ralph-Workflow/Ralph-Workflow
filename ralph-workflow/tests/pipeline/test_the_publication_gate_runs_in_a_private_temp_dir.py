from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ralph.pipeline.publication_gate import check_publication_gate

_COMMAND = 'printf "%s" "$TMPDIR" > seen_tmpdir.txt && touch "$TMPDIR/clr-debug-pipe-1-in"'


def _declare_gate(root: Path) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        f"# Verification policy\n\nRALPH-FACT: authoritative_verify_command: `{_COMMAND}`\n",
        encoding="utf-8",
    )


def _seen_tmpdir(root: Path) -> Path:
    check_publication_gate(root, "HEAD", tree_of=lambda _root, _sha: "a" * 40)
    return Path((root / "seen_tmpdir.txt").read_text(encoding="utf-8"))


def test_the_gate_command_never_writes_to_the_shared_temp_dir(tmp_path: Path) -> None:
    _declare_gate(tmp_path)
    shared = {Path(tempfile.gettempdir()).resolve()}
    if os.environ.get("TMPDIR"):
        shared.add(Path(os.environ["TMPDIR"]).resolve())

    seen = _seen_tmpdir(tmp_path)

    assert str(seen)
    assert seen.resolve() not in shared


def test_what_the_gate_leaves_in_its_temp_dir_is_removed_when_it_ends(tmp_path: Path) -> None:
    _declare_gate(tmp_path)

    seen = _seen_tmpdir(tmp_path)

    assert not seen.exists()
