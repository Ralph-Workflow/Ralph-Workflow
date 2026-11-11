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
    strategy_history: tuple[str, ...] = (),
) -> str:
    prompt_path = render_conflict_prompt(
        root=tmp_path,
        target="main",
        conflicted_paths=conflicted,
        round_index=round_index,
        round_cap=3,
        surviving_marker_paths=surviving,
        strategy_history=strategy_history,
    )
    assert prompt_path is not None
    return prompt_path.read_text(encoding="utf-8")


def _render_rebase_stop(tmp_path: Path) -> str:
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


def test_conflict_prompt_leads_with_a_persisted_validation_hint_without_consuming_it(
    tmp_path: Path,
) -> None:
    hint_path = tmp_path / ".agent" / "tmp" / "last_retry_error_development.txt"
    hint_path.parent.mkdir(parents=True)
    hint = "VALIDATION FAILURE\nCONFLICT001: unresolved marker"
    hint_path.write_text(hint, encoding="utf-8")

    rendered = _render(tmp_path)

    assert rendered.startswith(hint)
    assert hint_path.read_text(encoding="utf-8") == hint


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
    rendered = _render(tmp_path)
    assert "mcp__ralph__declare_complete" in rendered
    assert "WITHOUT calling `mcp__ralph__declare_complete`" in rendered
    assert "FAILED" in rendered


def test_the_standard_unattended_preamble_is_present(tmp_path: Path) -> None:
    assert "UNATTENDED MODE" in _render(tmp_path)


def test_the_granted_capabilities_are_stated(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "SESSION CAPABILITIES (Granted by Ralph Workflow)" in rendered
    assert "workspace.write_tracked" in rendered


def test_the_brokered_mcp_tool_surface_is_stated(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "MCP TOOLS (Ralph Workflow Brokered)" in rendered
    assert "mcp__ralph__write_file" in rendered


def test_the_preamble_does_not_offer_artifact_submission(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert "ARTIFACT SUBMISSION" not in rendered
    assert "submit_artifact" not in rendered
    assert "declare_complete" in rendered


def test_editing_an_unlisted_path_is_forbidden_outright(tmp_path: Path) -> None:
    rendered = _render(tmp_path)
    assert 'Do not modify ANY file that is not listed under "Conflicted files"' in rendered
    assert "strictly required" not in rendered


def test_the_rebase_variant_states_the_unlisted_path_enforcement(
    tmp_path: Path,
) -> None:
    assert "REJECTS the whole resolution and aborts the\n  rebase" in _render_rebase_stop(tmp_path)


def test_the_endpoint_merge_variant_claims_no_unenforced_gate(
    tmp_path: Path,
) -> None:
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


def test_prompt_carries_durable_strategy_history(tmp_path: Path) -> None:
    rendered = _render(
        tmp_path,
        strategy_history=(
            "rebase_resolver: unresolved shared.txt",
            "refresh_retry: target moved",
        ),
    )

    assert "Earlier integration strategies" in rendered
    assert "rebase_resolver: unresolved shared.txt" in rendered
    assert "refresh_retry: target moved" in rendered


def test_prompt_carries_no_project_payload(tmp_path: Path) -> None:
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    for forbidden in ("PROMPT.md", "PLAN.md", ".agent/PLAN.md"):
        assert forbidden not in rendered


def test_prompt_carries_none_of_the_in_graph_payload_headings(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))
    for heading in (
        "## Payload",
        "## Task",
        "EXECUTION PLAN",
        "ORIGINAL REQUEST",
        "PRODUCT_CRITERIA.md",
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
    rendered = _render(tmp_path, conflicted=("a/one.py", "b/two.md", "c/three.txt"))
    assert "main" in rendered
    for path in ("a/one.py", "b/two.md", "c/three.txt"):
        assert path in rendered


def test_rendering_is_idempotent_for_the_same_round(tmp_path: Path) -> None:
    first = _render(tmp_path)
    second = _render(tmp_path)
    assert first == second


def test_the_shipped_skills_guidance_is_present(tmp_path: Path) -> None:
    rendered = _render(tmp_path)

    assert "SHIPPED SKILLS" in rendered
    assert "use only task-relevant skills" in rendered.lower()
    assert "do not search unrelated skill directories" in rendered.lower()


def test_the_skills_block_does_not_displace_the_completion_contract(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path)

    assert "declare_complete" in rendered
    assert 'Do not modify ANY file that is not listed under "Conflicted files"' in rendered


def test_the_skills_block_does_not_widen_the_conflict_only_context(
    tmp_path: Path,
) -> None:
    rendered = _render(tmp_path, round_index=2, surviving=("src/alpha.py",))

    for forbidden in (
        "PROMPT.md",
        "PLAN.md",
        ".agent/PLAN.md",
        "## Payload",
        "## Task",
        "EXECUTION PLAN",
        "ORIGINAL REQUEST",
        "PRODUCT_CRITERIA.md",
        "ANALYSIS FEEDBACK",
    ):
        assert forbidden not in rendered
