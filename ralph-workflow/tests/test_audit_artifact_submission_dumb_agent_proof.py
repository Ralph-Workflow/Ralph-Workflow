"""Dumb-agent-proof properties of the shared artifact submission macro.

A "dumb" or cheap/weak agent (small model, low reasoning budget, large
context noise) reliably follows instructions only when the instructions
are:

- numbered and procedural (not nested prose),
- contain a worked example showing the EXACT JSON shape to send,
- state what success looks like (declare_complete or "you are done"),
- forbid the common confusions explicitly (DO NOT / NEVER),
- are short enough to keep the key steps in the attention window.

This test pins those properties on the shared macro so cheap models
cannot miss the submission contract, and so per-template prose cannot
silently regress to a verbose paragraph the agent ignores.

The previous failure mode: the agent submitted a successfully-received
artifact (the receipt was stamped) but the gate's "no artifact" check
ran anyway because the agent skipped a step (e.g. the post-submit
declare_complete, or wrapping the payload in an outer envelope) and
the human-facing completion message lied. The macro has to be
unambiguous enough that the agent cannot do that again.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATES_DIR = Path("ralph/prompts/templates")
MACRO_PATH = TEMPLATES_DIR / "shared" / "_artifact_submission.j2"


def _read_macro() -> str:
    return MACRO_PATH.read_text(encoding="utf-8")


def test_macro_uses_numbered_procedure_not_buried_prose() -> None:
    """The macro MUST present the submission as a numbered step procedure.

    Cheap models follow numbered steps. Prose paragraphs are skimmed and
    the second clause is dropped. The macro must contain at least three
    numbered steps that walk the agent through the submission.
    """
    content = _read_macro()
    # Look for "1.", "2.", "3." style numbered items — at minimum a
    # 3-step procedure.
    numbered_steps = content.count("\n1. ") + content.count("\n1) ")
    assert numbered_steps >= 1, (
        "shared macro must present the submission as a numbered procedure "
        "(at least step 1) so a cheap model cannot skip the steps"
    )
    for step in ("2.", "3."):
        assert f"\n{step} " in content, (
            f"shared macro must include numbered step {step} (cheap "
            f"models follow step-by-step; prose gets skimmed)"
        )


def test_macro_includes_a_worked_mcp_call_example() -> None:
    """The macro MUST show a concrete MCP call with the EXACT JSON shape.

    A worked example with the exact JSON shape (including escaped quotes
    inside ``content``) is the single highest-value sentence for cheap
    models. The example must:
    - show ``artifact_type`` and ``content`` keys explicitly,
    - use a non-empty ``content`` JSON string in the example (not ``""``),
    - render the canonical ``{{ artifact_type }}`` so each per-type
      render is self-describing.
    """
    content = _read_macro()
    # Look for a JSON code block with both keys present.
    assert '"artifact_type"' in content, (
        "macro must show a worked example containing the artifact_type key"
    )
    assert '"content"' in content, (
        "macro must show a worked example containing the content key"
    )
    # The example must use the artifact_type placeholder so the rendered
    # output is self-describing per template.
    assert '"{{ artifact_type }}"' in content or '"{{artifact_type}}"' in content, (
        "macro example must render {{ artifact_type }} so cheap models "
        "see the exact value they need to send"
    )


def test_macro_states_post_submit_success_criterion() -> None:
    """The macro MUST say what to do AFTER the submit succeeds.

    Without an explicit "after the tool call, do X" step, a cheap agent
    will either echo unnecessary prose or stop without calling
    ``declare_complete``, leaving the run looking incomplete. The macro
    must include a step that names the success criterion (the tool
    returns "Artifact submitted" or equivalent), and direct the agent
    to call ``declare_complete`` or otherwise signal done.
    """
    content = _read_macro()
    assert "declare_complete" in content, (
        "macro must direct the agent to call declare_complete after a "
        "successful submit; otherwise the agent stops mid-protocol and "
        "the gate sees no completion evidence"
    )


def test_macro_lists_explicit_do_not_warnings() -> None:
    """The macro MUST list every common confusion as a "do not" bullet.

    Cheap models pattern-match the negative space as well as the
    positive. The historical drift sources that the shared macro
    catches:
    - wrapping the payload in outer ``type`` / ``content`` fields,
    - using the bare alias ``commit`` / ``skip`` instead of the canonical
      ``commit_message`` artifact_type,
    - guessing the schema instead of reading the format doc.

    (The ``content_path`` legacy warning is intentionally NOT in the
    macro — it only applies to commit-class artifact types, and the
    per-template prose carries it where it belongs. Putting it in the
    shared macro would pollute non-commit templates like
    developer_iteration whose prompts explicitly test for its absence.)
    """
    content = _read_macro()
    for forbidden in (
        "type",
        "content",
        "commit_message",  # explicit canonical, not the alias "commit" / "skip"
    ):
        # Each forbidden concept must appear in a "do not" context. We
        # just check the token is mentioned; the surrounding prose must
        # warn against it. Cheap-model fragility is best reduced by
        # naming the trap explicitly, not by hiding it.
        assert forbidden in content, (
            f"shared macro must explicitly warn against the {forbidden!r} "
            f"confusion; cheap models will repeat any trap the macro fails "
            f"to name"
        )
    # The macro must have at least one explicit "Do NOT" or "Never" line.
    lowered = content.lower()
    assert "do not" in lowered or "never" in lowered, (
        "shared macro must contain at least one explicit 'do not' / "
        "'never' warning so cheap models notice the negative space"
    )


def test_macro_is_concise_enough_for_attention_window() -> None:
    """The macro MUST stay under 2000 chars of body text.

    Long prose is a cheap-model killer: the key submission step gets
    diluted. The macro body (after stripping the file-top comment) must
    be short enough that a cheap model can attend to every step.
    """
    content = _read_macro()
    # Strip the leading Jinja comment block (the one starting with ``{#``).
    if content.lstrip().startswith("{#"):
        end = content.find("#}")
        body = content[end + 2 :].strip()
    else:
        body = content
    assert len(body) < 2000, (
        f"shared macro body is {len(body)} chars; cheap models cannot "
        f"attend to the submission steps in a >2000-char block. Trim the "
        f"prose to the procedural steps + worked example."
    )


def test_macro_keeps_inner_payload_object_explicit() -> None:
    """The fallback path must say the INNER payload object — not a wrapped envelope.

    The historical bug: agents wrote
    ``{"type":"commit_message","content":{...}}`` to the fallback path,
    which the parser rejected. The macro must say "ONLY the inner payload
    object, NOT wrapped in type/content" with the negation stated twice
    (cheap models miss single-mention negations).
    """
    content = _read_macro()
    # The phrase "inner payload" must appear (positive statement).
    assert "inner payload" in content.lower()
    # The phrase "Do NOT wrap" or "do not wrap" must appear.
    assert "do not wrap" in content.lower() or "don't wrap" in content.lower(), (
        "shared macro must explicitly forbid wrapping the fallback file "
        "in an outer envelope; cheap models default to wrapping when "
        "told to write JSON to a file"
    )


def test_macro_names_canonical_artifact_type_in_example() -> None:
    """The macro's example must show the CANONICAL ``commit_message`` value,
    not the alias ``commit`` / ``skip``.

    The handler canonicalizes ``commit`` → ``commit_message``; a cheap
    model that sends ``artifact_type="commit"`` will get an error. The
    macro must show the canonical value explicitly.
    """
    content = _read_macro()
    # The example artifact_type in the rendered example must be the
    # canonical "commit_message" value (the canonicalization target for
    # the "commit" / "skip" aliases).
    assert "commit_message" in content, (
        "shared macro's worked example must use the canonical "
        "commit_message artifact_type, not the 'commit' / 'skip' alias"
    )
