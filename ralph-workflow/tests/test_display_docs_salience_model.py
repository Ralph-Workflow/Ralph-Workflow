"""S-8: prove the published final-model documents describe the shipped
salience-token model.

PLAN.md S-8 binds the prose in both ``docs/sphinx/display.rst`` and
``docs/sphinx/developer-internals.md`` to the code that actually ships,
so renaming the parameter or moving a call-site field fails the
docs test until both pages move with it. The check is per-page
because the brief's Definition of Done item 5 names both pages
explicitly, and a page that mentions the token without saying what
either call site bids with fails just as hard as a page that
describes the superseded role-alternation signal in affirmative
prose.

Why a test rather than a `grep` for "state token": a `grep` only
confirms the words appear, not that the surrounding sentence is
about the shipped mechanism. Both pages could satisfy the grep
while still describing the allocator in terms the shipped model
contradicts. The test reads the binding source of truth
(``SALIENCE_STATE_TOKEN_FIELDS``) and the keyword-only parameter
name off the function signature, then names each required
token (parameter + every entry's call-site + every entry's
token-field list) on each page.

The superseded claim is banned by an explicit phrase list, also
checked on both pages and on the docstring. The ban is on
affirmative statements of the replaced signal only; prose that
names role alternation as the thing that was replaced is expected
and allowed.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ralph.display.parallel_display import (
    SALIENCE_STATE_TOKEN_FIELDS,
    ParallelDisplay,
)


def _repo_root() -> Path:
    """Resolve the ralph-workflow repo root from this test file's path."""
    return Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _kwonly_param_names(func) -> list[str]:
    """Return the keyword-only parameter names of ``func``."""
    sig = inspect.signature(func)
    return [
        name
        for name, param in sig.parameters.items()
        if param.kind == inspect.Parameter.KEYWORD_ONLY
    ]


def _applies_kwonly(func, name: str) -> bool:
    return name in _kwonly_param_names(func)


def test_state_token_parameter_is_keyword_only_on_apply_salience() -> None:
    """The seam name the docs bind to is a real keyword-only parameter
    on ``_apply_salience``. Renaming the kwarg to ``_`` or making it
    positional-only breaks the test, which is exactly the case where
    the docs binding is also broken.
    """
    assert _applies_kwonly(ParallelDisplay._apply_salience, "state_token"), (
        "ParallelDisplay._apply_salience must carry a keyword-only "
        "`state_token` parameter so the docs can bind to the shipped "
        "model by name."
    )


def test_salience_state_token_fields_is_a_dict_of_str_tuples() -> None:
    """The published token-fields mapping must be the shape the docs
    bind against: ``{call_site_name: (token_field_1, ...)}``. A
    mapping that drops a call-site name or a field list silently
    widens the contract; this test fails the gate before the docs
    can pass.
    """
    assert isinstance(SALIENCE_STATE_TOKEN_FIELDS, dict)
    for call_site, fields in SALIENCE_STATE_TOKEN_FIELDS.items():
        assert isinstance(call_site, str), call_site
        assert isinstance(fields, tuple), (call_site, fields)
        assert all(isinstance(f, str) for f in fields), (call_site, fields)
        assert len(fields) >= 1, (
            f"call site {call_site!r} must declare at least one token "
            f"field; an empty tuple is not a real token."
        )


@pytest.mark.parametrize(
    ("doc_path", "banned_phrases"),
    (
        (
            _repo_root() / "docs" / "sphinx" / "display.rst",
            (
                "differs from the immediately preceding call's role",
                "the previous call's role",
            ),
        ),
        (
            _repo_root() / "docs" / "sphinx" / "developer-internals.md",
            (
                "differs from the immediately preceding call's role",
                "the previous call's role",
            ),
        ),
    ),
    ids=["display.rst", "developer-internals.md"],
)
def test_doc_does_not_assert_superseded_signal(doc_path: Path, banned_phrases: tuple[str, ...]) -> None:
    """A page that describes the new token model AND the old
    role-alternation signal in affirmative prose is a contradiction.
    The ban is on affirmative statements of the replaced signal only;
    prose that names role alternation as the thing that was replaced
    is expected and allowed (the plan permits that).
    """
    text = _read(doc_path)
    for phrase in banned_phrases:
        assert phrase not in text, (
            f"{doc_path.name} still asserts the superseded "
            f"role-alternation signal: {phrase!r}. Remove the "
            f"affirmative claim of the replaced signal; prose that "
            f"names the *old* behaviour as the replaced thing is OK."
        )


@pytest.mark.parametrize(
    ("doc_path",),
    (
        (_repo_root() / "docs" / "sphinx" / "display.rst",),
        (_repo_root() / "docs" / "sphinx" / "developer-internals.md",),
    ),
    ids=["display.rst", "developer-internals.md"],
)
def test_doc_names_state_token_keyword(doc_path: Path) -> None:
    """Both pages must name the keyword-only ``state_token`` parameter
    the shipped code requires the docs to bind against.
    """
    text = _read(doc_path)
    assert "state_token" in text, (
        f"{doc_path.name} does not name the `state_token` parameter "
        f"the shipped ``_apply_salience`` requires the docs to bind against."
    )


@pytest.mark.parametrize(
    ("doc_path",),
    (
        (_repo_root() / "docs" / "sphinx" / "display.rst",),
        (_repo_root() / "docs" / "sphinx" / "developer-internals.md",),
    ),
    ids=["display.rst", "developer-internals.md"],
)
def test_doc_names_every_token_field_for_every_call_site(doc_path: Path) -> None:
    """For each entry in ``SALIENCE_STATE_TOKEN_FIELDS`` (the published
    call-site / token-field mapping), the page must name both the
    call-site and every token field. A page that mentions the token
    abstractly but never says which call site bids with which field
    fails this gate.
    """
    text = _read(doc_path)
    missing: list[str] = []
    for call_site, fields in SALIENCE_STATE_TOKEN_FIELDS.items():
        if call_site not in text:
            missing.append(f"{call_site!r} (call-site name)")
        missing.extend(
            f"{field!r} (token field for {call_site!r})"
            for field in fields
            if field not in text
        )
    assert not missing, (
        f"{doc_path.name} is missing the following token-binding "
        f"terms from ``SALIENCE_STATE_TOKEN_FIELDS``: {missing}. "
        f"Both pages must name the call-site AND every token field."
    )


def test_apply_salience_docstring_does_not_assert_superseded_signal() -> None:
    """The docstring is the third binding surface (the two pages plus
    the function's own documentation). It must not assert the
    superseded role-alternation signal in affirmative prose.
    """
    docstring = ParallelDisplay._apply_salience.__doc__ or ""
    banned = (
        "differs from the immediately preceding call's role",
        "the previous call's role",
    )
    for phrase in banned:
        assert phrase not in docstring, (
            f"ParallelDisplay._apply_salience.__doc__ still asserts "
            f"the superseded role-alternation signal: {phrase!r}."
        )
