from __future__ import annotations

import re

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import agents_md, markers, validators
from ralph.workspace.memory import MemoryWorkspace


def _empty_stack() -> ProjectStack:
    return ProjectStack()


_V6_LOADING_ORDER = "execution-kernel.md first"

_OUTDATED_LOADING_ORDER = "the canonical kernel file first"

_READINESS_DIMENSIONS_RE = re.compile(
    r"structure complete=[a-z]+, "
    r"implementation verified=[a-z]+, "
    r"outcome evidence reviewed=[a-z]+"
)

_V2_BLOCK_BEGIN = "<!-- ralph-workflow-policy:begin v2 -->"


def _v2_placeholder_block() -> str:
    scratch = MemoryWorkspace()
    agents_md.bootstrap(scratch)
    return scratch.read(markers.AGENTS_MD).replace(markers.AGENTS_BLOCK_BEGIN, _V2_BLOCK_BEGIN)


def _v2_ready_block(*, structure: str, implementation: str, outcome: str) -> str:
    scratch = MemoryWorkspace()
    agents_md.bootstrap(scratch)
    agents_md.condense_placeholder_block(scratch)
    ready = scratch.read(markers.AGENTS_MD).replace(markers.AGENTS_BLOCK_BEGIN, _V2_BLOCK_BEGIN)
    return _READINESS_DIMENSIONS_RE.sub(
        f"structure complete={structure}, "
        f"implementation verified={implementation}, "
        f"outcome evidence reviewed={outcome}",
        ready,
    )


def _outdated_placeholder_block() -> str:
    scratch = MemoryWorkspace()
    agents_md.bootstrap(scratch)
    return scratch.read(markers.AGENTS_MD).replace(_V6_LOADING_ORDER, _OUTDATED_LOADING_ORDER)


def _outdated_ready_block(*, structure: str, implementation: str, outcome: str) -> str:
    scratch = MemoryWorkspace()
    agents_md.bootstrap(scratch)
    agents_md.condense_placeholder_block(scratch)
    ready = scratch.read(markers.AGENTS_MD).replace(_V6_LOADING_ORDER, _OUTDATED_LOADING_ORDER)
    return _READINESS_DIMENSIONS_RE.sub(
        f"structure complete={structure}, "
        f"implementation verified={implementation}, "
        f"outcome evidence reviewed={outcome}",
        ready,
    )


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
        "# AGENTS.md\n\n" + markers.OPT_OUT_MARKER + "\n\nThe rest of the file.\n",
    )
    assert agents_md.is_opted_out(ws) is True


def test_is_opted_out_rejects_near_miss_variants() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "<!-- ralph-workflow-policy: skip project -->\n")
    assert agents_md.is_opted_out(ws) is False
    ws2 = MemoryWorkspace()
    ws2.write(markers.AGENTS_MD, "<!-- ralph-workflow-policy:  skip  -->\n")
    assert agents_md.is_opted_out(ws2) is False
    ws3 = MemoryWorkspace()
    ws3.write(markers.AGENTS_MD, "<!-- RALPH-WORKFLOW-POLICY: SKIP -->\n")
    assert agents_md.is_opted_out(ws3) is False
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
    assert "effective bounded policy" in content
    assert "portfolio:" in content
    assert "Dead code is prohibited" in content
    assert "rewriting later is better" in content


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
    assert markers.AGENTS_MD not in second
    second_content = ws.read(markers.AGENTS_MD)
    assert first_content == second_content
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
    ws = MemoryWorkspace()
    original = f"# Original\n\nUser content.\n\n{markers.AGENTS_BLOCK_BEGIN}\nincomplete\n"
    ws.write(markers.AGENTS_MD, original)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_bootstrap_does_not_append_when_partial_end_only() -> None:
    ws = MemoryWorkspace()
    original = f"# Original\n\nUser content.\n\ntrailing\n{markers.AGENTS_BLOCK_END}\n"
    ws.write(markers.AGENTS_MD, original)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_bootstrap_does_not_append_when_duplicate_complete_block() -> None:
    ws = MemoryWorkspace()
    block = (
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, block + block)
    original = ws.read(markers.AGENTS_MD)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_bootstrap_does_not_append_when_misordered_markers() -> None:
    ws = MemoryWorkspace()
    original = (
        f"{markers.AGENTS_BLOCK_END}\n{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n"
    )
    ws.write(markers.AGENTS_MD, original)
    changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in changed
    assert ws.read(markers.AGENTS_MD) == original


def test_condense_replaces_untouched_placeholder_with_concise_block() -> None:
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
    assert "Dead code is prohibited" in content
    assert "rewriting later is better" in content
    begin = content.find(markers.AGENTS_BLOCK_BEGIN)
    end = content.find(markers.AGENTS_BLOCK_END)
    block_lines = content[begin:end].splitlines()
    assert len(block_lines) <= 17, "condensed block must stay bounded"


def test_condense_ready_block_requires_portfolio_and_public_evidence() -> None:
    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)

    assert agents_md.condense_placeholder_block(ws) == [markers.AGENTS_MD]

    content = ws.read(markers.AGENTS_MD)
    assert "effective bounded policy portfolio" in content
    assert "default-gate budget" in content
    assert "public-surface observation" in content
    assert "Telemetry," in content
    assert (
        "provenance, private-state inspection, and agent narration are not correctness proof."
        in content
    )


def test_condense_preserves_rewritten_block() -> None:
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
        "# Big existing policy\n\n" + "rule line\n" * 20 + markers.OPT_OUT_MARKER + "\n",
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


def test_managed_block_routing_satisfies_idempotency_and_readiness() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CANONICAL_DIR.rstrip("/"), "")
    routing_content = (
        f"# AGENTS.md\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"Quality policies live under `{markers.CANONICAL_DIR}`. All agent roles MUST\n"
        f"load policies in this order: `execution-kernel.md` first, then their role slice,\n"
        f"then any triggered domain policy before changing the project; this file's\n"
        f"source-of-truth and non-negotiables sections remain authoritative for agent\n"
        f"behaviour.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, routing_content)

    findings = [
        f for f in validators.validate_readiness(ws, ProjectStack()) if f.path == markers.AGENTS_MD
    ]
    assert not findings, f"validate AGENTS.md returned unexpected findings: {findings}"

    bootstrap_changed = agents_md.bootstrap(ws)
    assert markers.AGENTS_MD not in bootstrap_changed
    assert ws.read(markers.AGENTS_MD) == routing_content
    assert ws.read(markers.AGENTS_MD).count(markers.AGENTS_BLOCK_BEGIN) == 1
    assert ws.read(markers.AGENTS_MD).count(markers.AGENTS_BLOCK_END) == 1

    condense_changed = agents_md.condense_placeholder_block(ws)
    assert markers.AGENTS_MD not in condense_changed
    assert ws.read(markers.AGENTS_MD) == routing_content


def test_bootstrap_output_directs_v6_loading_order() -> None:
    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)
    content = ws.read(markers.AGENTS_MD)
    assert (
        "All agent roles MUST\n"
        "load policies in this order: execution-kernel.md first, then their role slice,\n"
        "then any triggered domain policy before changing the project."
    ) in content or (
        "load policies in this order: execution-kernel.md first, then their role slice, then any triggered domain policy"
        in " ".join(content.split())
    )


def test_condense_output_directs_v6_loading_order() -> None:
    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)
    agents_md.condense_placeholder_block(ws)
    content = ws.read(markers.AGENTS_MD)
    normalized = " ".join(content.split())
    assert (
        "load policies in this order: execution-kernel.md first, then their role slice, then any triggered domain policy before changing the project"
        in normalized
    )


def test_bootstrap_migrates_outdated_ralph_placeholder_idempotently() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    user_header = "# Project Title\n\nCustom user rules above.\n\n"
    user_footer = "\n\nCustom user rules below.\n"
    outdated_placeholder = _outdated_placeholder_block()
    ws.write(markers.AGENTS_MD, user_header + outdated_placeholder + user_footer)

    changed = agents_md.bootstrap(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert content.startswith(user_header)
    assert content.endswith(user_footer.lstrip("\n") if content.endswith("\n") else user_footer)
    assert "execution-kernel.md first" in content

    second_changed = agents_md.bootstrap(ws)
    assert second_changed == []
    assert ws.read(markers.AGENTS_MD) == content


def test_bootstrap_migrates_outdated_ralph_ready_block_idempotently() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    user_header = "# Project Title\n\n"
    outdated_ready = _outdated_ready_block(structure="true", implementation="false", outcome="true")
    ws.write(markers.AGENTS_MD, user_header + outdated_ready)

    changed = agents_md.bootstrap(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert content.startswith(user_header)
    assert "execution-kernel.md first" in content
    assert "structure complete=true" in content
    assert "implementation verified=false" in content
    assert "outcome evidence reviewed=true" in content

    assert agents_md.bootstrap(ws) == []
    assert ws.read(markers.AGENTS_MD) == content


def test_condense_migrates_outdated_ralph_ready_block_idempotently() -> None:
    ws = MemoryWorkspace()
    outdated_ready = _outdated_ready_block(structure="true", implementation="true", outcome="true")
    ws.write(markers.AGENTS_MD, outdated_ready)

    changed = agents_md.condense_placeholder_block(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert "execution-kernel.md first" in content
    assert "structure complete=true" in content

    assert agents_md.condense_placeholder_block(ws) == []
    assert ws.read(markers.AGENTS_MD) == content


def test_bootstrap_and_condense_preserve_user_authored_block() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    user_block = (
        f"# AGENTS.md\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"Custom team policy routing under {markers.CANONICAL_DIR}.\n"
        f"Do not overwrite this custom text.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, user_block)

    assert agents_md.bootstrap(ws) == []
    assert ws.read(markers.AGENTS_MD) == user_block

    assert agents_md.condense_placeholder_block(ws) == []
    assert ws.read(markers.AGENTS_MD) == user_block


def test_bootstrap_migrates_v2_placeholder_block_to_v3_idempotently() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    user_header = "# Project Title\n\nCustom user rules above.\n\n"
    user_footer = "\n\nCustom user rules below.\n"
    ws.write(markers.AGENTS_MD, user_header + _v2_placeholder_block() + user_footer)

    changed = agents_md.bootstrap(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert content.startswith(user_header)
    assert content.endswith("Custom user rules below.\n")
    assert _V2_BLOCK_BEGIN not in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert "execution-kernel.md first" in content
    begin = content.find(markers.AGENTS_BLOCK_BEGIN)
    end = content.find(markers.AGENTS_BLOCK_END)
    assert len(content[begin:end].splitlines()) <= agents_md.MANAGED_BLOCK_LINE_BUDGET

    assert agents_md.bootstrap(ws) == []
    assert ws.read(markers.AGENTS_MD) == content


def test_bootstrap_migrates_v2_ready_block_to_v3_preserving_dimensions() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    v2_ready = _v2_ready_block(structure="true", implementation="false", outcome="true")
    ws.write(markers.AGENTS_MD, v2_ready)

    changed = agents_md.bootstrap(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert _V2_BLOCK_BEGIN not in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert "structure complete=true" in content
    assert "implementation verified=false" in content
    assert "outcome evidence reviewed=true" in content

    assert agents_md.bootstrap(ws) == []
    assert ws.read(markers.AGENTS_MD) == content


def test_condense_migrates_v2_ready_block_to_v3_preserving_dimensions() -> None:
    ws = MemoryWorkspace()
    v2_ready = _v2_ready_block(structure="true", implementation="true", outcome="false")
    ws.write(markers.AGENTS_MD, v2_ready)

    changed = agents_md.condense_placeholder_block(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert _V2_BLOCK_BEGIN not in content
    assert "structure complete=true" in content
    assert "outcome evidence reviewed=false" in content

    assert agents_md.condense_placeholder_block(ws) == []
    assert ws.read(markers.AGENTS_MD) == content


def test_bootstrap_migrates_v1_marker_to_v3_idempotently() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    user_header = "# Project Title\n\nCustom user rules above.\n\n"
    user_footer = "\n\nCustom user rules below.\n"
    v1_block = (
        f"{markers.AGENTS_BLOCK_BEGIN_V1}\n"
        f"This project participates in Ralph Workflow's project-policy-readiness\n"
        f"preflight. Before ordinary planning or development begins, the canonical\n"
        f"quality-policy files under {markers.CANONICAL_DIR} must exist...\n"
        "The remediation agent MUST, in order:\n"
        "1. Inspect the project.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, user_header + v1_block + user_footer)

    changed = agents_md.bootstrap(ws)
    assert changed == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN_V1 not in content
    assert markers.AGENTS_BLOCK_BEGIN in content
    assert content.startswith(user_header)
    assert "execution-kernel.md first" in content

    second_changed = agents_md.bootstrap(ws)
    assert second_changed == []
    assert ws.read(markers.AGENTS_MD) == content


def test_bootstrap_preserves_user_authored_v1_block() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee `AGENTS.md` for project policy.\n")
    user_block = (
        f"# AGENTS.md\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN_V1}\n"
        f"Custom user-written policy instructions under {markers.CANONICAL_DIR}.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, user_block)
    assert agents_md.bootstrap(ws) == []
    assert ws.read(markers.AGENTS_MD) == user_block
