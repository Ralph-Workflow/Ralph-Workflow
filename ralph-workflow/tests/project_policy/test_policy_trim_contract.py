from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SHIPPED_POLICY_ROOT = REPOSITORY_ROOT / "ralph-workflow/ralph/project_policy/starters"
REPOSITORY_POLICY_ROOT = REPOSITORY_ROOT / "docs/ralph-workflow-policy"
CONTRIBUTING = REPOSITORY_ROOT / "ralph-workflow/CONTRIBUTING.md"

POLICY_COPIES: dict[str, Path] = {
    "shipped policy": SHIPPED_POLICY_ROOT,
    "repository policy": REPOSITORY_POLICY_ROOT,
}
RETIRED_BUNDLE_TERMS: tuple[str, ...] = (
    "29 skills",
    "29 default skills",
    "skills-package",
    "sync-upstream-skills",
)
POLICY_WINS_PATTERN: re.Pattern[str] = re.compile(
    r"Shipped skills hold technique; where\s+a\s+skill's guidance conflicts with\s+"
    r"this kernel, the kernel \(policy\) wins\.",
    re.IGNORECASE,
)
LIBRARY_DOCS_PATTERN: re.Pattern[str] = re.compile(
    r"When\s+the behaviour belongs to a third-party library, that library's own\s+"
    r"documentation is the spec: check it before relying on the behaviour\.",
    re.IGNORECASE,
)
PREMISE_CHECK_PATTERN: re.Pattern[str] = re.compile(
    r"test the premise shared by the failed fixes before another patch",
    re.IGNORECASE,
)


def _policy_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.suffix in {".md", ".toml"}
    )


def test_policy_copies_do_not_name_the_retired_skill_bundle() -> None:
    for copy_name, root in POLICY_COPIES.items():
        text = _policy_text(root).casefold()
        for term in RETIRED_BUNDLE_TERMS:
            assert term.casefold() not in text, f"{copy_name} names retired term {term!r}"


def test_policy_copies_state_policy_precedence_exactly_once() -> None:
    for copy_name, root in POLICY_COPIES.items():
        matches: list[str] = POLICY_WINS_PATTERN.findall(_policy_text(root))
        assert len(matches) == 1, f"{copy_name} has {len(matches)} policy-wins statements"


def test_policy_copies_have_at_most_one_library_documentation_sentence() -> None:
    for copy_name, root in POLICY_COPIES.items():
        matches: list[str] = LIBRARY_DOCS_PATTERN.findall(_policy_text(root))
        assert len(matches) <= 1, f"{copy_name} has {len(matches)} library-docs sentences"


def test_retained_premise_check_has_an_explicit_per_copy_exception_record() -> None:
    audit = CONTRIBUTING.read_text(encoding="utf-8")
    premise_row = re.search(
        r"(?im)^\|\s*Remove the premise-check rule\s*\|\s*"
        r"Consciously excepted under the trim-less clause\s*\|\s*"
        r"Consciously excepted under the trim-less clause\s*\|[^\n]*"
        r"(?:deliberate restoration|48bd5e8ca)",
        audit,
    )
    for copy_name, root in POLICY_COPIES.items():
        if not PREMISE_CHECK_PATTERN.search(_policy_text(root)):
            continue
        assert premise_row, (
            f"{copy_name} retains the premise check without an explicit exception row"
        )


def test_policy_trim_audit_records_per_copy_generic_rule_verdicts() -> None:
    audit = CONTRIBUTING.read_text(encoding="utf-8")
    assert re.search(r"(?im)^#{2,4}\s+Criterion-8 policy-trim verdicts\s*$", audit)
    header = re.compile(
        r"(?im)^\|\s*Criterion\s*\|\s*"
        r"`docs/ralph-workflow-policy/execution-kernel\.md`\s*\|\s*"
        r"`ralph/project_policy/starters/execution-kernel\.md`\s*\|\s*Evidence\s*\|$"
    )
    assert header.search(audit), "policy-trim audit must report both copies independently"
    generic_trim_row = re.compile(
        r"(?im)^\|\s*Trim generic test-first, evidence, and red-green technique[^|]*\|\s*"
        r"Satisfied\s*\|\s*Satisfied\s*\|"
    )
    assert generic_trim_row.search(audit), (
        "policy-trim audit must record both copies' generic-rule verdicts"
    )
