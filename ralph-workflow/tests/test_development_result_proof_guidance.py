"""Lock the development-result proof guidance contract (UNIT U-result).

The format doc ``ralph/mcp/artifacts/format_docs/development_result.md``
and the shared prompt partial
``ralph/prompts/templates/shared/_development_result_proof.jinja`` must
agree with the runtime behavior in ``ralph/phases/execution.py``:

- Plan-item proof IDs are shape-independent: on plan items the
  validator rejects only duplicate IDs, a missing or unknown
  ``Disposition``, a missing ``Rationale`` for
  ``adapted`` / ``not_applicable`` / ``blocked``, and ``blocked`` in a
  completed result (the artifact grammar gate, not proof validation).
  The UI design-evidence gate is shape-independent too: an item's
  *proof text* claiming the UI work triggers it, never the bracketed
  reference label.
- Analysis finding IDs are validated exactly: duplicate analysis-item
  proof entries and missing or unknown analysis finding IDs are hard
  errors raised by ``_analysis_proof_errors``.

The shipped documents' literal contents are the regression contract, so
the tests read them directly from the repo; a fixture copy would assert
against the fixture, not the shipped guidance.
"""

from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FORMAT_DOC = _REPO_ROOT / "ralph" / "mcp" / "artifacts" / "format_docs" / "development_result.md"
_FORMAT_DOC_EXAMPLE = (
    _REPO_ROOT / "ralph" / "mcp" / "artifacts" / "format_docs" / "examples" / "development_result.md"
)
_JINJA_PARTIAL = (
    _REPO_ROOT / "ralph" / "prompts" / "templates" / "shared" / "_development_result_proof.jinja"
)
_VALIDATOR_SPEC = _REPO_ROOT / "ralph" / "mcp" / "artifacts" / "markdown" / "specs" / "development_result.py"


def _normalized(path: Path) -> str:
    """Return the file's text with every whitespace run collapsed.

    The markdown source wraps long sentences across lines, so
    phrase-level assertions run against whitespace-normalized text.
    """
    return " ".join(path.read_text(encoding="utf-8").split())


def test_format_doc_drops_stale_plan_item_proof_gate_claims() -> None:
    """The stale proof-gate claims are gone: neither the "one item per
    usable extracted plan reference" requirement nor a "missing
    plan-item" hard error appears anywhere in the format doc."""
    text = _normalized(_FORMAT_DOC)

    assert "proof policy requires one item per usable extracted plan reference" not in text
    assert "missing plan-item" not in text


def test_format_doc_states_analysis_finding_ids_are_validated_exactly() -> None:
    """The format doc names what ``execution.py`` enforces at proof
    validation: duplicate analysis-item proof entries, and missing or
    unknown analysis finding IDs, validated exactly against the prior
    analysis's stable finding IDs."""
    text = _normalized(_FORMAT_DOC)

    assert "duplicate analysis-item proof entries" in text
    assert "missing or unknown analysis finding IDs" in text
    assert "analysis finding IDs are validated exactly" in text


def test_format_doc_scopes_plan_item_proof_to_actual_plan_references() -> None:
    """Plan-item proof asks for one item per plan reference the plan
    actually uses; coverage of the plan's intent is enforced through the
    development-analysis feedback loop, not by exact ID matching or a
    hard missing-entry error."""
    text = _normalized(_FORMAT_DOC)

    assert "one item per plan reference the plan actually uses" in text
    assert "not by exact ID matching or a hard missing-entry error" in text
    assert "not matching a plan-parsed ID; it only rejects duplicate IDs" not in text


def test_format_doc_partial_guidance_names_parallel_dispatch() -> None:
    """The partial-outcome guidance is role-aware: a coordinator with
    remaining independent ready slices dispatches them in parallel
    rather than handing each one back as a separate ``partial``; a
    worker continues in-scope recovery per
    ``shared/_no_exemption_for_failures.j2`` (the worker contract
    forbids dispatch, so the dispatch-in-parallel rule does not
    apply to a worker reading the format doc).

    The previous text instructed *any* reader to "dispatch them in
    parallel", which contradicted the worker's no-dispatch contract.
    The lock here keeps the role-aware contract from regressing to
    a role-agnostic dispatch instruction.
    """
    text = _normalized(_FORMAT_DOC)

    # Coordinator-side dispatch guidance survives the rewrite.
    assert "coordinator who still owns independent ready" in text
    assert "slices dispatches them in parallel" in text
    # Worker-side guidance points at the canonical role-correct rule
    # so workers are not told to dispatch.
    assert "shared/_no_exemption_for_failures.j2" in text
    # The unqualified "dispatch them in parallel" sentence is gone so
    # the format doc does not contradict the worker no-dispatch
    # contract when it is read via the submission macro in
    # ``worker_developer.jinja``.
    assert "dispatch them in parallel rather than" not in text
    assert "dispatch them in parallel rather than completing" not in text


def _rendered_partial() -> str:
    """Render the shipped jinja partial exactly as the prompt engine does."""
    template = Environment().from_string(_JINJA_PARTIAL.read_text(encoding="utf-8"))
    return " ".join(template.render().split())


def test_rendered_partial_scopes_shape_independence_to_plan_references() -> None:
    """The rendered partial scopes shape independence to ``## Plan Items
    Proven`` and carries no unscoped shape-independence claim or "only
    rejects duplicate IDs" understatement."""
    rendered = _rendered_partial()

    assert "Proof IDs in `## Plan Items Proven` are shape-independent" in rendered
    assert "**Proof IDs are shape-independent.**" not in rendered
    assert "not matching a plan-parsed ID; it only rejects duplicate IDs" not in rendered


def test_rendered_partial_requires_exact_analysis_finding_id_match() -> None:
    """The rendered partial states that ``## Analysis Items Addressed``
    IDs must match the prior analysis's stable finding IDs and that a
    missing or unknown analysis finding ID fails proof validation."""
    rendered = _rendered_partial()

    assert "must match the prior analysis's stable finding ID exactly" in rendered
    assert "missing or unknown analysis finding ID fails proof validation" in rendered


def test_format_doc_states_ui_gate_judges_proof_text_not_reference_label() -> None:
    """The format doc says the UI design-evidence gate keys on proof text
    that claims the UI work, never on the bracketed reference label, so
    acceptance does not depend on how a plan reference is spelled."""
    text = _normalized(_FORMAT_DOC)

    assert "judged from the item's proof text, never from the bracketed" in text
    assert "reference label" in text


def test_rendered_partial_states_ui_gate_judges_proof_text_not_reference_label() -> None:
    """The rendered partial scopes the design-evidence requirement to
    proof text that claims the UI work, not the reference label."""
    rendered = _rendered_partial()

    assert "judged from the item's proof text, never from the bracketed" in rendered
    assert "reference label" in rendered


def test_rendered_partial_scopes_rationale_to_non_completed_dispositions() -> None:
    """The rendered partial scopes the Rationale requirement to adapted,
    not_applicable, and blocked items: completed items must not require
    one. The shared partial must not claim Rationale is rejected for
    completed plan items.
    """
    rendered = _rendered_partial()

    assert (
        "A missing `Rationale` is rejected only for `adapted` / "
        "`not_applicable` / `blocked` items"
    ) in rendered
    assert "completed items do not" in rendered
    assert "missing Disposition / Rationale fields" not in rendered


def test_format_doc_states_cycle_warning_runtime_clock_plus_declared_flag() -> None:
    """The format doc names both sources the spec consults: the run's
    own published clock for the relevant timer (cycle or development),
    and the matching declared frontmatter flag
    (``cycle_timebox_warned: true`` or ``development_timebox_warned: true``)
    honoured for replays and hand-written reports.
    """
    text = _normalized(_FORMAT_DOC)

    assert "Whether the timebox warned is read from the run's own published clock" in text
    assert "cycle or development" in text
    assert "matching declared frontmatter flag" in text
    assert "cycle_timebox_warned: true" in text
    assert "development_timebox_warned: true" in text
    assert (
        "a result validated outside the warned invocation (a replay or a "
        "hand-written report) keeps its stricter reading"
    ) in text


def test_validator_docstring_scopes_rationale_to_non_completed_dispositions() -> None:
    """DA-015: the development-result validator docstring agrees with the
    shared proof partial on which dispositions require ``Rationale``.

    The shared partial correctly scopes ``Rationale`` rejection to
    ``adapted`` / ``not_applicable`` / ``blocked`` items. The validator
    docstring must agree — a blanket "missing Disposition / Rationale
    fields" claim contradicts the partial and misrepresents the
    runtime, so the docstring is rewritten to mirror the partial's
    scoped wording.
    """
    text = " ".join(_VALIDATOR_SPEC.read_text(encoding="utf-8").split())

    assert "missing Disposition / Rationale fields" not in text
    assert "a missing ``Disposition``" in text
    assert (
        "and (for ``adapted`` / ``not_applicable`` / ``blocked`` items) a missing ``Rationale``"
    ) in text


def test_rendered_partial_names_timebox_only_mechanical_gate() -> None:
    """The rendered partial states the mechanical presence gate: carry the
    section whenever there is work to prove, while the validator
    mechanically requires it only for a ``completed`` result once the
    run's cycle timebox or development timebox has warned — read from the
    live clock or the declared frontmatter flag."""
    rendered = _rendered_partial()

    assert "Carry this section whenever there is work to prove" in rendered
    assert (
        "the validator mechanically requires it only for a `completed` result"
        in rendered
    )
    assert "cycle timebox or development timebox has warned" in rendered
    assert "cycle_timebox_warned" in rendered
    assert "development_timebox_warned" in rendered


def test_rendered_partial_keeps_each_proof_rule_in_one_home() -> None:
    """The plan-item validator-rejection rule and the coverage-enforcement
    rule each appear exactly once: the ``## Plan Items Proven`` paragraph
    must not restate the shape-independence paragraph's rules."""
    rendered = _rendered_partial()

    assert (
        rendered.count(
            "never rejects a plan-item ID solely for not matching a plan-parsed ID"
        )
        == 1
    )
    assert rendered.count("development analysis feedback loop (U-2)") == 1


# ---------------------------------------------------------------------------
# U-3R: role agreement between the format doc and the worker template.
#
# The format doc is read by workers via the submission macro in
# ``worker_developer.jinja``. The previous text instructed *any* reader
# to "dispatch them in parallel", which contradicted the worker's
# no-dispatch contract from ``_worker_verification.jinja``. The dispatch
# sentence is now qualified as coordinator-only; workers continue
# in-scope recovery per ``shared/_no_exemption_for_failures.j2``.
# These tests pin that role-aware contract from regressing.
# ---------------------------------------------------------------------------


def test_format_doc_does_not_direct_workers_to_dispatch() -> None:
    """The format doc must not direct every reader to dispatch in parallel.

    Workers read this format doc via the submission macro in
    ``worker_developer.jinja``. A worker receiving a "dispatch them
    in parallel" instruction is sent straight into a contract
    contradiction: ``_worker_verification.jinja`` forbids dispatch,
    while the format doc tells it to dispatch. The lock here keeps the
    role agreement between the format doc and the worker template.
    """
    text = _normalized(_FORMAT_DOC)

    # The unqualified "dispatch them in parallel" sentence is gone so
    # a worker reader does not receive a directive that contradicts
    # the worker contract.
    assert "dispatch them in parallel" not in text, (
        "format doc still tells every reader to dispatch; the worker "
        "contract forbids dispatch — qualify the sentence as "
        "coordinator-only or drop it"
    )


def test_format_doc_partial_guidance_is_role_aware() -> None:
    """The partial-outcome guidance keeps the canonical partial
    rule intact AND qualifies the dispatch instruction as
    coordinator-only with a worker-side pointer to the canonical
    role-correct rule.

    Both halves of the role-aware contract are pinned: the
    coordinator-side dispatch guidance survives, and the worker-side
    pointer directs a worker reader to the canonical role-correct
    partial rule rather than contradicting it.
    """
    text = _normalized(_FORMAT_DOC)

    # Coordinator-side dispatch guidance survives the rewrite.
    assert "coordinator who still owns independent ready" in text
    assert "slices dispatches them in parallel" in text
    # Worker-side guidance points at the canonical role-correct rule
    # so a worker reader is told to continue in-scope recovery rather
    # than dispatch.
    assert "shared/_no_exemption_for_failures.j2" in text
    # Canonical partial-rule phrases remain intact so the role-aware
    # rewrite does not accidentally drop the partial/failed landmarks.
    for phrase in (
        "Use `partial` only when",
        "physical-world action",
        "operator-only credential or decision",
        "After submitting `partial`, call `declare_complete`",
        "Use `failed` when no safe actionable continuation",
    ):
        assert phrase in text, f"format doc lost canonical phrase: {phrase!r}"


# ---------------------------------------------------------------------------
# U-3E: the canonical example models the free-form plan-reference contract.
#
# The format doc documents that ``## Plan Items Proven`` accepts
# shape-independent references (step ID, work-unit bracket ID,
# prose-plan ID, subplan / section heading, or any other stable
# reference the plan actually uses). A renderer that copies the
# example sees a ``S-1..S-N`` synthetic scheme and reads the plan as
# one that demands numeric step IDs; the example therefore must NOT
# use that shape as its only shape.
# ---------------------------------------------------------------------------


def _example_plan_item_ids() -> list[str]:
    """Return every bracketed ID under the example's ``## Plan Items Proven`` section."""
    text = _FORMAT_DOC_EXAMPLE.read_text(encoding="utf-8")
    section_start = text.index("## Plan Items Proven")
    after_start = section_start + len("## Plan Items Proven")
    next_heading = text.find("\n## ", after_start)
    section = text[after_start:next_heading] if next_heading >= 0 else text[after_start:]
    return re.findall(r"^- \[([^\]]+)\]", section, flags=re.MULTILINE)


def test_format_doc_example_uses_free_form_plan_references() -> None:
    """U-3E: the canonical example must model the free-form plan-reference
    contract documented in the format doc.

    The format doc accepts step IDs, work-unit bracket IDs,
    prose-plan IDs, subplan / section headings, and any other stable
    reference the plan actually uses. A renderer that copies the
    example sees a ``S-1..S-N`` synthetic scheme and reads the plan
    as one that demands numeric step IDs; the example therefore must
    not use that shape as its only shape.

    The check is "the example must use at least one non-``S-n`` plan
    reference" so a renderer sees a free-form anchor to copy when it
    has no numeric steps. A regression that reverts the example to
    ``S-1..S-6`` only (no prose heading, no work-unit ID) would
    tell a renderer the contract requires numeric IDs.
    """
    plan_ids = _example_plan_item_ids()
    assert plan_ids, "example is missing `## Plan Items Proven` bullets"

    # No S-n synthetic scheme: a renderer that sees [S-1], [S-2] ...
    # reads the plan as one that demands numeric step IDs.
    synthetic_ids = [pid for pid in plan_ids if re.fullmatch(r"S-\d+", pid)]
    assert not synthetic_ids, (
        f"example still uses synthetic step IDs {synthetic_ids!r}; "
        "the format doc documents free-form references (work-unit IDs, "
        "prose headings) so the example must model that shape"
    )

    # At least one non-numeric free-form ID is required to make the
    # contract visible to renderers that copy the example verbatim.
    free_form_ids = [pid for pid in plan_ids if not re.fullmatch(r"S-\d+", pid)]
    assert free_form_ids, (
        "example has only synthetic step IDs; the format doc accepts "
        "free-form references, so the example must model at least one"
    )


def test_format_doc_example_still_demonstrates_diverse_dispositions() -> None:
    """U-3E: the example demonstrates ``completed``, ``adapted``, and
    ``not_applicable`` dispositions. Switching the IDs from synthetic
    step numbers to free-form plan references must not accidentally
    drop the disposition coverage — the example is the place readers
    see all three in one place.
    """
    text = _FORMAT_DOC_EXAMPLE.read_text(encoding="utf-8")
    for disposition in ("completed", "adapted", "not_applicable"):
        assert disposition in text, (
            f"example lost the {disposition!r} disposition; the example "
            "must demonstrate all three so renderers see how to mark "
            "each kind of plan reference"
        )
