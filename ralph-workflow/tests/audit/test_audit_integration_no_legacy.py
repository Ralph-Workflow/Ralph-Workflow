from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing.audit_integration_no_legacy import (
    DENIAL_ONLY_PRE_RENAME_SENTINELS,
    audit_modules,
    audit_tree,
    main,
    resolve_repository_root,
    selftest,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_audit_tree_ignores_generated_and_runtime_directories": "the repository-wide source audit must ignore the canonical Sphinx output tree that the verification workflow generates before invoking the audit",
}


def test_audit_integration_no_legacy_selftest() -> None:
    assert selftest() == 0


_STEM = "commit_" + "cleanup"
_HYPHEN_STEM = "commit-" + "cleanup"
_RULE = f"legacy {_STEM} identifier reappeared"


def test_audit_flags_legacy_review_identifiers() -> None:
    planted_snippets = [
        ("ralph/pipeline/planted_cleanup_call.py", f"def test(): return {_STEM}()\n"),
        ("ralph/pipeline/planted_phase.py", f"phase = 'development_{_STEM}'\n"),
        ("ralph/pipeline/planted_final_phase.py", f"phase = 'development_final_{_STEM}'\n"),
        ("ralph/pipeline/planted_skill.py", f"skill = 'submit-{_HYPHEN_STEM}-artifact'\n"),
        ("ralph/pipeline/planted_template.py", f"template = '{_STEM}.jinja'\n"),
        ("ralph/pipeline/planted_counter.py", f"{_STEM}_rejections += 1\n"),
        ("ralph/pipeline/probe.py", 'phase = "commit_" + "cleanup"\n'),
        ("ralph/pipeline/planted_implicit_concat.py", 'phase = "commit_" "cleanup"\n'),
        (
            "ralph/pipeline/planted_nested_concat.py",
            'phase = "development_" + "commit_" + "cleanup"\n',
        ),
        ("ralph/pipeline/planted_split_commit.py", 'phase = "com" + "mit_cleanup"\n'),
        ("ralph/pipeline/planted_split_cleanup.py", 'phase = "commit_clea" + "nup"\n'),
        (
            "ralph/pipeline/planted_split_both.py",
            'phase = "com" + "mit_clea" + "nup"\n',
        ),
    ]
    for rel, source in planted_snippets:
        violations = audit_modules({rel: source}, ignore_transitional=True)
        rules = [v.rule for v in violations]
        assert _RULE in rules, f"Failed for {rel}: got {rules}"


def test_audit_allows_exact_denial_only_pre_rename_sentinel() -> None:
    denial_path = "ralph/pipeline/_checkpoint_retired_phase_error.py"
    assert (denial_path, _STEM) in DENIAL_ONLY_PRE_RENAME_SENTINELS
    assert DENIAL_ONLY_PRE_RENAME_SENTINELS[(denial_path, _STEM)]

    source = (
        "class CheckpointRetiredPhaseError:\n"
        "    def __init__(self) -> None:\n"
        '        retired_artifact = "commit_" + "cleanup"\n'
    )
    violations = audit_modules({denial_path: source}, ignore_transitional=True)
    assert not [v for v in violations if v.rule == _RULE]


def test_audit_rejects_non_exact_folded_legacy_on_denial_sentinel_path() -> None:
    denial_path = "ralph/pipeline/_checkpoint_retired_phase_error.py"
    violations = audit_modules(
        {
            denial_path: 'phase = "development_" + "commit_" + "cleanup"\n',
        },
        ignore_transitional=True,
    )
    assert _RULE in [v.rule for v in violations]


def test_audit_allows_clean_commit_review_identifiers() -> None:
    clean_snippets = {
        "ralph/pipeline/clean_review_call.py": "def test(): return commit_review()\n",
        "ralph/pipeline/clean_phase.py": "phase = 'development_commit_review'\n",
        "ralph/pipeline/clean_final_phase.py": "phase = 'development_final_commit_review'\n",
        "ralph/pipeline/clean_skill.py": "skill = 'submit-commit-review-artifact'\n",
        "ralph/pipeline/clean_template.py": "template = 'commit_review.jinja'\n",
        "ralph/pipeline/clean_counter.py": "commit_review_rejections += 1\n",
    }
    violations = audit_modules(clean_snippets, ignore_transitional=True)
    assert not violations


def test_audit_exempts_historical_fixture_paths() -> None:
    exempt_source = {
        "tests/agents/invoke/fixtures/claude_interactive_real_capture/session.jsonl": (
            f'{{"names": ["submit-{_HYPHEN_STEM}-artifact"]}}\n'
        ),
    }
    violations = audit_modules(exempt_source, ignore_transitional=True)
    assert not violations


def test_audit_tree_detects_legacy_review_files(tmp_path: Path) -> None:
    ralph_dir = tmp_path / "ralph"
    ralph_dir.mkdir()
    (ralph_dir / "clean.py").write_text("commit_review = True\n", encoding="utf-8")

    assert not audit_tree(tmp_path)

    tpl_dir = ralph_dir / "prompts" / "templates"
    tpl_dir.mkdir(parents=True)
    planted_tpl = tpl_dir / f"{_STEM}.jinja"
    planted_tpl.write_text("legacy template\n", encoding="utf-8")

    violations = audit_tree(tmp_path)
    rules = [v.rule for v in violations]
    assert _RULE in rules


def _project_layout(root: Path) -> Path:
    package_dir = root / "ralph-workflow"
    (package_dir / "ralph").mkdir(parents=True)
    (package_dir / "ralph" / "clean.py").write_text("commit_review = True\n", encoding="utf-8")
    return package_dir


def test_audit_default_root_reports_repository_root_docs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_dir = _project_layout(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "stray.md").write_text(f"phase `{_STEM}` is retired\n", encoding="utf-8")

    monkeypatch.chdir(package_dir)
    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "docs/stray.md" in captured.out
    assert _RULE in captured.out


def test_audit_default_root_accepts_clean_repository_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_dir = _project_layout(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "clean.md").write_text("phase `commit_review` is current\n", encoding="utf-8")

    monkeypatch.chdir(package_dir)
    exit_code = main([])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "OK" in captured.out


def test_audit_explicit_root_argument_scans_that_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _project_layout(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "stray.md").write_text(f"phase `{_STEM}` is retired\n", encoding="utf-8")

    exit_code = main([str(tmp_path)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "docs/stray.md" in captured.out
    assert _RULE in captured.out


def test_audit_root_scoped_package_paths_keep_package_and_pipeline_checks() -> None:
    package_rules = [
        violation.rule
        for violation in audit_modules(
            {
                "ralph-workflow/ralph/pipeline/planted_classifier.py": (
                    "def test(): return classify_conflict_attempt()\n"
                )
            },
            ignore_transitional=True,
        )
    ]
    assert "legacy conflict-attempt classifier reappeared" in package_rules

    pipeline_rules = [
        violation.rule
        for violation in audit_modules(
            {
                "ralph-workflow/ralph/pipeline/planted_private_resolver.py": (
                    "def test(): return _resolve_rebase_in_progress()\n"
                )
            },
            ignore_transitional=True,
        )
    ]
    assert "private resolver caller outside entry" in pipeline_rules

    legacy_module_rules = [
        violation.rule
        for violation in audit_modules(
            {
                "ralph-workflow/ralph/pipeline/conflict_resolution/resolution_outcome.py": (
                    "class ResolutionOutcome: pass\n"
                )
            },
            ignore_transitional=True,
        )
    ]
    assert "legacy resolution outcome module reappeared" in legacy_module_rules


def test_audit_tree_exempts_root_prompt_specification_only(tmp_path: Path) -> None:
    _project_layout(tmp_path)
    (tmp_path / "PROMPT.md").write_text(
        f"| role / artifact type `{_STEM}` | `commit_review` |\n", encoding="utf-8"
    )

    assert not audit_tree(tmp_path)

    (tmp_path / "notes.md").write_text(f"phase `{_STEM}` is retired\n", encoding="utf-8")

    violations = audit_tree(tmp_path)
    assert [violation.rule for violation in violations] == [_RULE]
    assert violations[0].path == "notes.md"


def test_audit_tree_ignores_generated_and_runtime_directories(tmp_path: Path) -> None:
    _project_layout(tmp_path)
    for relative in (
        ".venv/lib",
        "tmp",
        ".agent/raw",
        ".pi-subagents",
        ".omo/evidence",
        ".mypy_cache",
        "ralph-workflow/docs/sphinx/_build/html",
    ):
        directory = tmp_path / relative
        directory.mkdir(parents=True)
        (directory / "planted.md").write_text(f"phase `{_STEM}` is retired\n", encoding="utf-8")

    assert not audit_tree(tmp_path)


def test_audit_tree_ignores_machine_managed_worktrees(tmp_path: Path) -> None:
    _project_layout(tmp_path)
    for relative in ("wt-01-worker", "worktree-agent-02"):
        directory = tmp_path / relative
        directory.mkdir()
        (directory / "planted.md").write_text(f"phase `{_STEM}` is retired\n", encoding="utf-8")

    assert not audit_tree(tmp_path)


def test_resolve_repository_root_prefers_the_repository_root(tmp_path: Path) -> None:
    package_dir = tmp_path / "ralph-workflow"
    (package_dir / "ralph").mkdir(parents=True)
    nested = package_dir / "ralph" / "testing"
    nested.mkdir()

    assert resolve_repository_root(nested) == tmp_path.resolve()


def test_resolve_repository_root_falls_back_to_the_package_root(tmp_path: Path) -> None:
    package_root = tmp_path / "standalone"
    (package_root / "ralph").mkdir(parents=True)

    assert resolve_repository_root(package_root) == package_root.resolve()


_THREE_SHA_TEXT = "integrated_feature_sha, pre_target_sha, or pre_feature_sha"
_THREE_SHA_RULE = "retired three-SHA landed-proof message reappeared"


def test_audit_flags_retired_three_sha_message_text() -> None:
    planted = {
        "ralph/pipeline/planted_three_sha_literal.py": f'msg = "{_THREE_SHA_TEXT}"\n',
        "ralph/pipeline/planted_three_sha_fstring.py": f'msg = f"{_THREE_SHA_TEXT}"\n',
        "ralph-workflow/ralph/pipeline/planted_three_sha_pkg.py": (
            f'reason = "HEAD does not match {_THREE_SHA_TEXT}"\n'
        ),
    }
    for rel, source in planted.items():
        violations = audit_modules({rel: source}, ignore_transitional=True)
        rules = [v.rule for v in violations]
        assert _THREE_SHA_RULE in rules, f"Failed for {rel}: got {rules}"


def test_audit_allows_live_three_sha_identifier_variants() -> None:
    live_variants = {
        "ralph/pipeline/clean_stop_fallback.py": (
            "return record.resolving_stop_sha or record.pre_target_sha or record.pre_feature_sha\n"
        ),
        "ralph/pipeline/clean_landed_fields.py": (
            "fields = (\n"
            "    f'integrated_feature_sha={record.integrated_feature_sha or \"unset\"}',\n"
            "    f'pre_target_sha={record.pre_target_sha or \"unset\"}',\n"
            "    f'pre_feature_sha={record.pre_feature_sha}',\n"
            ")\n"
        ),
        "ralph/pipeline/clean_identity_pair.py": (
            "return current == (record.pre_feature_sha, record.pre_target_sha)\n"
        ),
    }
    violations = audit_modules(live_variants, ignore_transitional=True)
    assert not [v for v in violations if v.rule == _THREE_SHA_RULE]


def test_audit_allows_historical_three_sha_fixture_outside_ralph() -> None:
    fixture = {
        "tests/scenarios/_ralphsite_worktree_copy.py": (
            f'"salvage:resolver_agent": "failed: {_THREE_SHA_TEXT}"\n'
        ),
    }
    violations = audit_modules(fixture, ignore_transitional=True)
    assert not [v for v in violations if v.rule == _THREE_SHA_RULE]
