from __future__ import annotations

import pytest

from ralph.onboarding import STARTER_PROMPT_SENTINEL, resolve_starter_template


def test_resolve_starter_template_shapes_are_distinct() -> None:
    labels = ("feature-spec", "guardrail", "refactor", "test-coverage", "docs")
    templates = [resolve_starter_template(label) for label in labels]

    assert len(set(templates)) == len(labels)
    assert all(template.startswith(STARTER_PROMPT_SENTINEL) for template in templates)
    assert resolve_starter_template("guardrail") == resolve_starter_template("bug-fix")


def test_resolve_starter_template_unknown_label_lists_valid_names() -> None:
    with pytest.raises(
        ValueError, match="feature-spec, guardrail/bug-fix, refactor, test-coverage, docs"
    ):
        resolve_starter_template("unknown")
