from __future__ import annotations

from pathlib import Path

from ralph.testing.audit_integration_no_legacy import (
    audit_modules,
    audit_tree,
    selftest,
)


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
    ]
    for rel, source in planted_snippets:
        violations = audit_modules({rel: source}, ignore_transitional=True)
        rules = [v.rule for v in violations]
        assert _RULE in rules, f"Failed for {rel}: got {rules}"


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
