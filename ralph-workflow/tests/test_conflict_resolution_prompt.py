"""Tests for the conflict-only resolution prompt.

The load-bearing assertion is the NEGATIVE one: the resolution session
must carry the conflict and nothing else, so the rendered prompt may not
contain the run's project payload. A resolution agent that can see the
plan is an agent that may resume feature work inside an in-progress
merge.
"""

from __future__ import annotations

from pathlib import Path

from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt

_CONFLICTED = ("src/alpha.py", "docs/beta.md")


def _render(
    tmp_path: Path,
    *,
    round_index: int = 1,
    surviving: tuple[str, ...] = (),
    conflicted: tuple[str, ...] = _CONFLICTED,
) -> str:
    prompt_path = render_conflict_prompt(
        root=tmp_path,
        target="main",
        conflicted_paths=conflicted,
        round_index=round_index,
        round_cap=3,
        surviving_marker_paths=surviving,
    )
    assert prompt_path is not None
    return prompt_path.read_text(encoding="utf-8")


def _render_rebase_stop(tmp_path: Path) -> str:
    """Render the REBASE variant, which the endpoint-merge one differs from."""
    prompt_path = render_conflict_prompt(
        root=tmp_path,
        target="main",
        conflicted_paths=_CONFLICTED,
        round_index=1,
        round_cap=3,
        surviving_marker_paths=(),
        replaying_commit_sha="0123456789abcdef",
        replaying_commit_subject="feat: the replayed commit",
        stop_index=1,
        stop_cap=10,
    )
    assert prompt_path is not None
    return prompt_path.read_text(encoding="utf-8")


def test_every_conflicted_path_appears(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    for path in _CONFLICTED:
        assert path in rendered


def test_target_branch_and_repo_root_appear(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "main" in rendered
    assert str(tmp_path) in rendered


def test_completion_contract_is_stated(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "declare_complete" in rendered


def test_exiting_without_declare_complete_is_stated_to_be_a_failed_round(
    tmp_path: Path,
) -> None:
    """AC-05: the completion contract is a contract, not a suggestion.

    A resolution session that returns without calling ``declare_complete``
    is a failed round, and the prompt has to say so: the pipeline's
    invocation-succeeded input is derived from that call, so an agent
    that does not know the rule can silently burn a round.
    """
    rendered = _render(tmp_path)
    assert "mcp__ralph__declare_complete" in rendered
    assert "WITHOUT calling `declare_complete`" in rendered
    assert "FAILED" in rendered


def test_the_standard_unattended_preamble_is_present(tmp_path: Path) -> None:
    """AC-02: the resolution session gets the same standard preamble."""
    assert "UNATTENDED MODE" in _render(tmp_path)


def test_the_granted_capabilities_are_stated(tmp_path: Path) -> None:
    """AC-02: the agent is told what it is authorized to do.

    Rendered from the resolution drain's own defaults, so the prompt
    cannot drift into describing a capability set the session is not
    actually granted.
    """
    rendered = _render(tmp_path)
    assert "SESSION CAPABILITIES (Granted by Ralph Workflow)" in rendered
    assert "workspace.write_tracked" in rendered


def test_the_brokered_mcp_tool_surface_is_stated(tmp_path: Path) -> None:
    """AC-02: native file tools are disabled, so the prompt must say so.

    An agent that is not told to edit through ``write_file`` reaches for
    a native tool the MCP broker has turned off and burns the round.
    """
    rendered = _render(tmp_path)
    assert "MCP TOOLS (Ralph Workflow Brokered)" in rendered
    assert "mcp__ralph__write_file" in rendered


def test_the_preamble_does_not_offer_artifact_submission(tmp_path: Path) -> None:
    """The completion contract is ``declare_complete`` and nothing else.

    Offering the artifact surface would invite a ``development_result``
    for work that is not a development iteration, and the payload-free
    contract this prompt exists to keep would be the first casualty.
    """
    rendered = _render(tmp_path)
    assert "ARTIFACT SUBMISSION" not in rendered
    assert "submit_artifact" not in rendered
    assert "declare_complete" in rendered


def test_editing_an_unlisted_path_is_forbidden_outright(tmp_path: Path) -> None:
    """The prompt and ``rebase_loop``'s enforcement must agree.

    ``_stage_and_prove`` stages only the conflicted paths, so an edit the
    prompt permits elsewhere would be neither committed nor rejected. The
    prohibition is therefore absolute, with no 'strictly required'
    escape hatch.
    """
    rendered = _render(tmp_path)
    assert 'Do not modify ANY file that is not listed under "Conflicted files"' in rendered
    assert "strictly required" not in rendered


def test_the_rebase_variant_states_the_unlisted_path_enforcement(
    tmp_path: Path,
) -> None:
    """Only the rebase loop enforces it, so only the rebase prompt claims it."""
    assert "REJECTS the whole resolution and aborts the\n  rebase" in _render_rebase_stop(
        tmp_path
    )


def test_the_endpoint_merge_variant_claims_no_unenforced_gate(
    tmp_path: Path,
) -> None:
    """A prompt that promises a gate the merge path does not run would lie."""
    assert "re-reads the worktree after you return" not in _render(tmp_path)


def test_round_counter_is_rendered(tmp_path: Path) -> None:
    assert "Round 2 of 3" in _render(tmp_path, round_index=2)


def test_first_round_carries_no_surviving_marker_feedback(tmp_path: Path) -> None:
    rendered = _render(tmp_path, round_index=1, surviving=())
    assert "Why this round exists" not in rendered


def test_later_round_names_the_surviving_files(tmp_path: Path) -> None:
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    assert "Why this round exists" in rendered
    assert "src/alpha.py" in rendered


def test_prompt_carries_no_project_payload(tmp_path: Path) -> None:
    """The session sees the conflict only -- never the run's plan or prompt."""
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    for forbidden in ("PROMPT.md", "PLAN.md", ".agent/PLAN.md"):
        assert forbidden not in rendered


def test_prompt_carries_none_of_the_in_graph_payload_headings(
    tmp_path: Path,
) -> None:
    """AC-05: the payload headings the in-graph templates use must be absent.

    The negative contract is the load-bearing one. A resolution agent
    that can see the run's task or execution plan is an agent that may
    resume feature work inside an in-progress merge, so no heading that
    would carry one may survive a future edit of the template.
    """
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    for heading in (
        "## Payload",
        "## Task",
        "EXECUTION PLAN",
        "ORIGINAL REQUEST",
        "CURRENT_PROMPT.md",
    ):
        assert heading not in rendered


def test_unlistable_conflicts_fall_back_to_a_search_instruction(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path, conflicted=())
    assert "could not be listed" in rendered


def test_the_agent_is_told_not_to_run_git(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "DO NOT run any git command" in rendered


def test_the_rendered_prompt_names_the_target_and_every_conflicted_path(
    tmp_path: Path,
) -> None:
    """AC-05: the operator surface is bound to the conflict it is resolving."""
    rendered = _render(tmp_path, conflicted=("a/one.py", "b/two.md", "c/three.txt"))
    assert "main" in rendered
    for path in ("a/one.py", "b/two.md", "c/three.txt"):
        assert path in rendered


def test_rendering_is_idempotent_for_the_same_round(tmp_path: Path) -> None:
    first = _render(tmp_path)
    second = _render(tmp_path)
    assert first == second
