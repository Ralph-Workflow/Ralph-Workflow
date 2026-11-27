from __future__ import annotations

import pytest

from ralph.project_policy import added_lint_bypasses


def _diff(path: str, *added: str, removed: tuple[str, ...] = ()) -> str:
    body = "\n".join([*(f"-{line}" for line in removed), *(f"+{line}" for line in added)])
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}\n"


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("app/javascript/route.tsx", "  // eslint-disable-next-line no-explicit-any"),
        ("app/javascript/route.tsx", "/* eslint-disable */"),
        ("app/javascript/route.tsx", "  // @ts-ignore"),
        ("app/javascript/route.tsx", "  // @ts-expect-error legacy"),
        ("src/a.ts", "// @ts-nocheck"),
        ("src/a.ts", "  /* istanbul ignore next */"),
        ("src/a.ts", "  // biome-ignore lint/suspicious: x"),
        ("app/models/user.rb", "  def go # rubocop:disable Metrics/MethodLength"),
        ("app/models/user.rb", "# typed: false"),
        ("ralph/x.py", "import os  #" + " noqa: F401"),
        ("ralph/x.py", "x = f()  # type: ignore[arg-type]"),
        ("ralph/x.py", "x = f()  # pyright: ignore"),
        ("ralph/x.py", "# pylint: disable=too-many-branches"),
        ("cmd/main.go", "x := y //nolint:errcheck"),
        ("src/lib.rs", "#[allow(clippy::too_many_lines)]"),
    ],
)
def test_an_added_inline_suppression_is_a_bypass(path: str, line: str) -> None:
    assert added_lint_bypasses(_diff(path, line))


@pytest.mark.parametrize(
    ("path", "added", "removed"),
    [
        ("eslint.config.ts", ("    'max-lines': 'off',",), ()),
        ("eslint.config.ts", ('    "complexity": 0,',), ()),
        (".rubocop.yml", ("  Enabled: false",), ()),
        (".rubocop.yml", ("  Max: 400",), ("  Max: 100",)),
        ("tsconfig.json", ('    "strict": false,',), ()),
        ("tsconfig.json", ('    "noImplicitAny": false,',), ()),
        ("pyproject.toml", ("ignore_missing_imports = true",), ()),
        ("pyproject.toml", ('extend-ignore = ["TRY003"]',), ()),
        ("pyproject.toml", ('"ralph/**/*.py" = ["TRY003"]',), ()),
        ("setup.cfg", ("ignore_errors = True",), ()),
    ],
)
def test_loosening_a_lint_or_type_check_configuration_is_a_bypass(
    path: str, added: tuple[str, ...], removed: tuple[str, ...]
) -> None:
    assert added_lint_bypasses(_diff(path, *added, removed=removed))


@pytest.mark.parametrize(
    ("path", "added", "removed"),
    [
        (
            "docs/ralph-workflow-policy/linting-policy.md",
            ("Never add `# noqa` or `@ts-ignore`.",),
            (),
        ),
        ("CHANGELOG.md", ("- removed the last `eslint-disable`",), ()),
        ("app/javascript/route.tsx", ("const label = 'disable the button';",), ()),
        (".rubocop.yml", ("  Max: 50",), ("  Max: 100",)),
        ("eslint.config.ts", ("    'max-lines': ['error', 500],",), ()),
        ("eslint.config.ts", ("  ignores: ['vendor/**', 'app/assets/builds/**'],",), ()),
        (".rubocop.yml", ("    - 'db/schema.rb'",), ()),
        ("vendor/lib/thing.js", ("// eslint-disable-next-line",), ()),
        ("app/javascript/generated/api.ts", ("// @ts-nocheck",), ()),
    ],
)
def test_strengthening_rules_prose_and_generated_or_vendored_code_are_not_bypasses(
    path: str, added: tuple[str, ...], removed: tuple[str, ...]
) -> None:
    assert added_lint_bypasses(_diff(path, *added, removed=removed)) == []


def test_each_bypass_names_its_file_and_line() -> None:
    found = added_lint_bypasses(_diff("src/a.ts", "ok();", "  // @ts-ignore"))

    assert found == ["src/a.ts: // @ts-ignore"]
