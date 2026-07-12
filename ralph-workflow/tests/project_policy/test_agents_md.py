"""Tests for the AGENTS.md / CLAUDE.md opt-out and bootstrap behavior.

All tests use MemoryWorkspace (no real filesystem I/O) per the repository
test policy.
"""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import agents_md, markers
from ralph.workspace.memory import MemoryWorkspace


def _empty_stack() -> ProjectStack:
    return ProjectStack()


def test_is_opted_out_returns_false_when_agents_md_missing() -> None:
    ws = MemoryWorkspace()
    assert agents_md.is_opted_out(ws) is False


def test_is_opted_out_returns_false_when_marker_missing() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# AGENTS.md\n\nJust some content.\n")
    assert agents_md.is_opted_out(ws) is False


def test_is_opted_out_returns_true_when_marker_exact() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        "# AGENTS.md\n\n"
        + markers.OPT_OUT_MARKER
        + "\n\nThe rest of the file.\n",
    )
    assert agents_md.is_opted_out(ws) is True


def test_is_opted_out_rejects_near_miss_variants() -> None:
    # extra words
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "<!-- ralph-workflow-policy: skip project -->\n")
    assert agents_md.is_opted_out(ws) is False
    # whitespace change
    ws2 = MemoryWorkspace()
    ws2.write(markers.AGENTS_MD, "<!-- ralph-workflow-policy:  skip  -->\n")
    assert agents_md.is_opted_out(ws2) is False
    # uppercase
    ws3 = MemoryWorkspace()
    ws3.write(markers.AGENTS_MD, "<!-- RALPH-WORKFLOW-POLICY: SKIP -->\n")
    assert agents_md.is_opted_out(ws3) is False
    # trailing token
    ws4 = MemoryWorkspace()
    ws4.write(markers.AGENTS_MD, "<!-- ralph-workflow-policy: skipx -->\n")
    assert agents_md.is_opted_out(ws4) is False


def test_bootstrap_creates_agents_md_when_missing() -> None:
    ws = MemoryWorkspace()
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD in changed
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.AGENTS_BLOCK_END in content
    assert markers.CANONICAL_DIR in content


def test_bootstrap_preserves_existing_agents_md_content() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# Original\n\nUser-authored content.\n")
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD in changed
    content = ws.read(markers.AGENTS_MD)
    assert "# Original" in content
    assert "User-authored content." in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert markers.AGENTS_BLOCK_END in content


def test_bootstrap_is_idempotent() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# Original\n\nUser content.\n")
    first = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD in first
    first_content = ws.read(markers.AGENTS_MD)
    second = agents_md.bootstrap(ws)
    # Second call is a no-op for AGENTS.md.
    assert markers.AGENTS_MD not in second
    second_content = ws.read(markers.AGENTS_MD)
    # The first byte of pre-existing content is preserved exactly.
    assert first_content == second_content
    # Exactly one managed block is present.
    assert first_content.count(markers.AGENTS_BLOCK_BEGIN) == 1
    assert first_content.count(markers.AGENTS_BLOCK_END) == 1


def test_bootstrap_creates_claude_md_when_missing() -> None:
    ws = MemoryWorkspace()
    changed = agents_md.bootstrap(ws)
    assert markers.CLAUDE_MD in changed
    content = ws.read(markers.CLAUDE_MD)
    assert "AGENTS.md" in content


def test_bootstrap_preserves_claude_md_with_existing_agents_ref() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n")
    original = ws.read(markers.CLAUDE_MD)
    changed = agents_md.bootstrap(ws)
    # CLAUDE.md already references AGENTS.md -> no change.
    assert markers.CLAUDE_MD not in changed
    assert ws.read(markers.CLAUDE_MD) == original


def test_bootstrap_appends_pointer_to_claude_md_when_missing_ref() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nNo reference here.\n")
    changed = agents_md.bootstrap(ws)
    assert markers.CLAUDE_MD in changed
    content = ws.read(markers.CLAUDE_MD)
    assert "No reference here." in content
    assert "AGENTS.md" in content


def test_bootstrap_does_not_append_when_partial_begin_only() -> None:
    """A pre-existing AGENTS.md with begin but no end marker MUST NOT be
    silently appended-to: doing so creates a second begin marker that
    the validator would then flag and bootstrap cannot clear. The
    remediation agent must reconcile the partial state in one change.
    """
    ws = MemoryWorkspace()
    original = (
        "# Original\n\n"
        "User content.\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        "incomplete\n"
    )
    ws.write(markers.AGENTS_MD, original)
    changed = agents_md.bootstrap(ws)
    # Bootstrap is a no-op for AGENTS.md on malformed state.
    assert markers.AGENTS_MD not in changed
    # Content is preserved byte-for-byte (no duplicate begin appended).
    assert ws.read(markers.AGENTS_MD) == original


def test_bootstrap_does_not_append_when_partial_end_only() -> None:
    ws = MemoryWorkspace()
    original = (
        "# Original\n\n"
        "User content.\n\n"
        "trailing\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, original)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_bootstrap_does_not_append_when_duplicate_complete_block() -> None:
    """A pre-existing AGENTS.md with two complete managed blocks MUST NOT
    have a third block appended; that would push the duplicate count
    higher and the validator would still flag the file.
    """
    ws = MemoryWorkspace()
    block = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, block + block)
    original = ws.read(markers.AGENTS_MD)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_bootstrap_does_not_append_when_misordered_markers() -> None:
    """A managed block with the end marker appearing before the begin
    marker is structurally malformed. Bootstrap must NOT append another
    block that would compound the defect.
    """
    ws = MemoryWorkspace()
    original = (
        f"{markers.AGENTS_BLOCK_END}\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n"
    )
    ws.write(markers.AGENTS_MD, original)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_condense_replaces_untouched_placeholder_with_concise_block() -> None:
    """The bootstrap placeholder is TEMPORARY: once the project is READY it
    is condensed to a short pointer block. User content outside the block
    is preserved byte-for-byte."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# My project\n\nUser-authored intro.\n")
    agents_md.bootstrap(ws)

    changed = agents_md.condense_placeholder_block(ws)

    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert content.startswith("# My project\n\nUser-authored intro.\n")
    assert content.count(markers.AGENTS_BLOCK_BEGIN) == 1
    assert content.count(markers.AGENTS_BLOCK_END) == 1
    assert "The remediation agent MUST" not in content
    assert markers.CANONICAL_DIR in content
    begin = content.find(markers.AGENTS_BLOCK_BEGIN)
    end = content.find(markers.AGENTS_BLOCK_END)
    block_lines = content[begin:end].splitlines()
    assert len(block_lines) <= 10, "condensed block must stay short"


def test_condense_preserves_rewritten_block() -> None:
    """A block the agent or user rewrote is NOT the placeholder; leave it."""
    ws = MemoryWorkspace()
    custom = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"Read the policies under {markers.CANONICAL_DIR} first.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, custom)

    assert agents_md.condense_placeholder_block(ws) == []
    assert ws.read(markers.AGENTS_MD) == custom


def test_condense_is_noop_on_missing_or_malformed_agents_md() -> None:
    ws = MemoryWorkspace()
    assert agents_md.condense_placeholder_block(ws) == []
    ws.write(markers.AGENTS_MD, f"{markers.AGENTS_BLOCK_BEGIN}\nno end marker\n")
    assert agents_md.condense_placeholder_block(ws) == []


def test_condense_is_idempotent() -> None:
    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)
    assert agents_md.condense_placeholder_block(ws) == [markers.AGENTS_MD]
    condensed = ws.read(markers.AGENTS_MD)
    assert agents_md.condense_placeholder_block(ws) == []
    assert ws.read(markers.AGENTS_MD) == condensed


def test_significant_content_false_when_agents_md_missing() -> None:
    ws = MemoryWorkspace()
    assert agents_md.has_significant_unmanaged_content(ws) is False


def test_significant_content_false_when_managed_block_present() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        "# Big existing policy\n\n"
        + "rule line\n" * 20
        + f"{markers.AGENTS_BLOCK_BEGIN}\nbody\n{markers.AGENTS_BLOCK_END}\n",
    )
    assert agents_md.has_significant_unmanaged_content(ws) is False


def test_significant_content_false_when_opt_out_marker_present() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        "# Big existing policy\n\n"
        + "rule line\n" * 20
        + markers.OPT_OUT_MARKER
        + "\n",
    )
    assert agents_md.has_significant_unmanaged_content(ws) is False


def test_significant_content_false_below_line_threshold_without_heading() -> None:
    ws = MemoryWorkspace()
    nonempty = markers.SIGNIFICANT_NONEMPTY_LINE_THRESHOLD - 1
    ws.write(markers.AGENTS_MD, "just a plain line\n" * nonempty + "\n\n")
    assert agents_md.has_significant_unmanaged_content(ws) is False


def test_significant_content_true_with_single_heading() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# Our agent rules\n")
    assert agents_md.has_significant_unmanaged_content(ws) is True


def test_significant_content_true_at_line_threshold_without_heading() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        "plain rule line\n" * markers.SIGNIFICANT_NONEMPTY_LINE_THRESHOLD,
    )
    assert agents_md.has_significant_unmanaged_content(ws) is True


def test_write_opt_out_appends_marker_preserving_content() -> None:
    ws = MemoryWorkspace()
    original = "# Our agent rules\n\nDo the right thing.\n"
    ws.write(markers.AGENTS_MD, original)

    assert agents_md.write_opt_out(ws) == [markers.AGENTS_MD]

    content = ws.read(markers.AGENTS_MD)
    assert content.startswith(original)
    assert markers.OPT_OUT_MARKER in content
    assert agents_md.is_opted_out(ws) is True


def test_write_opt_out_second_call_does_not_duplicate_marker() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# Our agent rules\n")
    agents_md.write_opt_out(ws)
    once = ws.read(markers.AGENTS_MD)
    assert agents_md.write_opt_out(ws) == []
    assert ws.read(markers.AGENTS_MD) == once
