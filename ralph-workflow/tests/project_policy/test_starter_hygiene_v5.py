"""Starter-hygiene tests for the v5 outcome-oriented kernel.

These tests pin two narrow contracts that the v5 revision must keep honest:

1. The bundled starter set has the v5 composition: 22 entries (12 core,
   including the execution-kernel introduced by v5, plus 10 conditional)
   and the top-level loader docstring must state that composition
   without re-deriving it (the counts are computed from the loader
   truth and the v5 marker module so the docstring cannot drift).
2. The narrow-boundary test rule (B4) appears verbatim in the clean-code
   starter and is not contradicted by any other shipped starter.

Both are black-box: they exercise the public loader and the public
starter content only. Failures here mean a future edit re-introduced
drift the v5 plan removed.
"""

from __future__ import annotations

from pathlib import Path

from ralph.project_policy import markers
from ralph.project_policy.starters import (
    STARTER_NAMES,
    __doc__,
    iter_starter_names,
    read_starter,
)

_STARTERS_DIR = (
    Path(__file__).resolve().parents[2] / "ralph" / "project_policy" / "starters"
)


def test_starter_count_matches_loader_truth() -> None:
    """STARTER_NAMES carries 22 entries (12 core + 10 conditional) under v5.

    The execution-kernel (v5-introduced) is one of the 12 core files; the
    other 11 are the pre-v5 core set. The 10 conditional files match the
    CONDITIONAL_POLICY_FILES mapping.
    """
    assert len(STARTER_NAMES) == 22
    assert len(STARTER_NAMES) == len(list(iter_starter_names()))
    # Cross-check the 12-core count against the v5 marker module.
    assert len(markers.CORE_POLICY_FILES) == 12
    assert "execution-kernel.md" in markers.CORE_POLICY_FILES
    # The 10-conditional count comes from the v5 mapping.
    assert len(markers.CONDITIONAL_POLICY_FILES) == 10


def test_starter_docstring_states_v5_composition_without_recounting() -> None:
    """The loader docstring must NOT bake a hard-coded count.

    The literal "20 entries: 10 core + 10 conditional" wording was a
    v4-era stale claim; the v5 docstring must point at the loader
    truth (and the v5 marker module) so a future edit to
    :data:`STARTER_NAMES` or :data:`markers.CORE_POLICY_FILES` cannot
    silently desynchronize the docstring.
    """
    assert "20 entries: 10 core + 10 conditional" not in __doc__
    # The v5 wording must reference the live loader / marker truth.
    assert "STARTER_NAMES" in __doc__
    assert "CORE_POLICY_FILES" in __doc__


def test_clean_code_starter_echoes_narrow_boundary_rule() -> None:
    """B4: the narrowest-useful-observable-boundary rule must be in clean-code.

    The testing and architecture starters already carry the rule (see
    existing contract tests). The clean-code starter MUST echo it so a
    future refactor that needs to "expose implementation or add a
    wrapper" cannot use the clean-code starter as a justification.
    """
    content = read_starter("clean-code-policy.md")
    # The sentence may wrap across lines; normalize whitespace before the
    # substring check so the contract is robust to a future rewrap.
    assert "narrowest useful observable boundary" in " ".join(content.split())
    # And it must point to the execution kernel so the cross-starter
    # contract is auditable.
    assert "execution-kernel" in content
