"""Focused tests for the fenced artifact example verification audit."""

from __future__ import annotations

from importlib.util import find_spec

import pytest

import ralph.testing.audit_fenced_artifact_examples as audit_module

_VALID_ISSUES = """---
type: issues
status: no_issues
---
## Summary
- [SUM-1] The implementation is correct and verification passed.
"""


def test_fenced_artifact_example_audit_is_available() -> None:
    assert find_spec("ralph.testing.audit_fenced_artifact_examples") is not None


def test_static_fenced_artifact_validates_with_inferred_registered_spec() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = f"Example:\n\n```markdown\n{_VALID_ISSUES}```\n"

    assert check("review.jinja", source) == []


def test_explicit_fence_declaration_handles_frontmatter_type_alias() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """```markdown artifact=commit_message
---
type: skip
reason: No committable changes remain.
---
```
"""

    assert check("commit_message.jinja", source) == []


def test_format_doc_declaration_routes_invalid_example_through_real_validator() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """```markdown
---
type: issues
status: no_issues
---
```
"""

    violations = check("issues.md", source, declared_artifact_type="issues")

    assert len(violations) == 1
    assert "SPEC008" in violations[0]
    assert "missing required section 'Summary'" in violations[0]


def test_unknown_artifact_like_fence_fails_closed() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """```markdown
---
type: invented_artifact
---
## Summary
- [SUM-1] Looks plausible but has no registered spec.
```
"""

    violations = check("unknown.jinja", source)

    assert len(violations) == 1
    assert "no registered artifact spec" in violations[0]
    assert "invented_artifact" in violations[0]


def test_every_concrete_plan_fence_is_validated_without_pseudo_plan_exemptions() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """```markdown artifact=plan example-size=tiny
---
type: plan
---
## Summary
This deliberately incomplete tiny plan must be rejected.
```

```markdown artifact=plan example-size=large
---
type: plan
---
## Summary
This deliberately incomplete large plan must also be rejected.
```
"""

    violations = check("plan.md", source, declared_artifact_type="plan")

    assert sum("[PLAN022]" in item for item in violations) == 2


def test_plan_example_coverage_requires_tiny_medium_and_large_fan_in_shapes() -> None:
    check_coverage = getattr(audit_module, "check_plan_example_coverage", None)
    assert callable(check_coverage)
    source = """```markdown artifact=plan example-size=tiny
---
type: plan
---
## Steps
### [S-1] Make the bounded change
### [S-2] Verify it
```

```markdown artifact=plan example-size=medium
---
type: plan
---
## Steps
### [S-1] Test
### [S-2] Implement
### [S-3] Verify
```

```markdown artifact=plan example-size=large
---
type: plan
---
## Work Units
- [api] Implement the API slice
  Directories: src/api/
- [cli] Implement the CLI slice
  Directories: src/cli/
- [docs] Update operator documentation
  Directories: docs/
- [tests] Add cross-slice tests
  Directories: tests/
- [verify] Integrate the slices and run fan-in verification
  Depends on: api, cli, docs, tests
```
"""

    assert check_coverage("plan.md", source) == []


def test_plan_example_coverage_rejects_missing_large_fan_in_example() -> None:
    check_coverage = getattr(audit_module, "check_plan_example_coverage", None)
    assert callable(check_coverage)
    source = """```markdown artifact=plan example-size=tiny
---
type: plan
---
## Steps
### [S-1] Change and verify one file
```

```markdown artifact=plan example-size=medium
---
type: plan
---
## Steps
### [S-1] Test
### [S-2] Implement
### [S-3] Verify
```
"""

    violations = check_coverage("plan.md", source)

    assert violations == [
        "plan.md:1 plan examples must include example-size=large"
    ]


def test_non_artifact_and_generic_schematic_fences_are_ignored() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """```bash
$ make verify
```

```markdown
---
type: <artifact_type>
key: value
---
## Section
- [ID-1] generic grammar only
```
"""

    assert check("artifact_formats_index.md", source) == []


def test_submission_macro_example_uses_declared_type_and_renders_placeholders() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """{% set issues_example %}
---
type: issues
status: no_issues
---
## Summary
- [SUM-1] Work unit {{ unit_id }} passed.
{% endset %}
{{ render_artifact_submission(
    'issues',
    SUBMIT_MD_ARTIFACT_TOOL_REFERENCE,
    issues_example
) }}
"""

    assert check("review.jinja", source) == []


def test_submission_macro_invalid_example_reports_validator_diagnostic() -> None:
    check = getattr(audit_module, "check_source_examples", None)
    assert callable(check)
    source = """{% set issues_example %}
---
type: issues
status: no_issues
---
{% endset %}
{{ render_artifact_submission('issues', TOOL, issues_example) }}
"""

    violations = check("review.jinja", source)

    assert len(violations) == 1
    assert "SPEC008" in violations[0]
    assert "review.jinja" in violations[0]


def test_main_exit_codes_follow_collected_violations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    audit_main = getattr(audit_module, "main", None)
    assert callable(audit_main)
    monkeypatch.setattr(audit_module, "collect_violations", lambda: [])
    assert audit_main([]) == 0
    monkeypatch.setattr(
        audit_module,
        "collect_violations",
        lambda: ["review.jinja:9 [SPEC008] missing required section 'Summary'"],
    )
    assert audit_main([]) == 1
    output = capsys.readouterr().out
    assert "FENCED ARTIFACT EXAMPLE AUDIT FAILED" in output
    assert "review.jinja:9" in output


@pytest.mark.timeout_seconds(5)
def test_current_prompt_and_format_doc_examples_all_validate() -> None:
    collect = getattr(audit_module, "collect_violations", None)
    assert callable(collect)
    violations = collect()
    assert violations == [], "fenced artifact example violations:\n" + "\n".join(violations)
