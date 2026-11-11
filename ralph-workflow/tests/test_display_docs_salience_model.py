from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ralph.display.parallel_display import (
    SALIENCE_STATE_TOKEN_FIELDS,
    ParallelDisplay,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _kwonly_param_names(func) -> list[str]:
    sig = inspect.signature(func)
    return [
        name
        for name, param in sig.parameters.items()
        if param.kind == inspect.Parameter.KEYWORD_ONLY
    ]


def _applies_kwonly(func, name: str) -> bool:
    return name in _kwonly_param_names(func)


def test_state_token_parameter_is_keyword_only_on_apply_salience() -> None:
    assert _applies_kwonly(ParallelDisplay._apply_salience, "state_token"), (
        "ParallelDisplay._apply_salience must carry a keyword-only "
        "`state_token` parameter so the docs can bind to the shipped "
        "model by name."
    )


def test_salience_state_token_fields_is_a_dict_of_str_tuples() -> None:
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
def test_doc_does_not_assert_superseded_signal(
    doc_path: Path, banned_phrases: tuple[str, ...]
) -> None:
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
    text = _read(doc_path)
    missing: list[str] = []
    for call_site, fields in SALIENCE_STATE_TOKEN_FIELDS.items():
        if call_site not in text:
            missing.append(f"{call_site!r} (call-site name)")
        missing.extend(
            f"{field!r} (token field for {call_site!r})" for field in fields if field not in text
        )
    assert not missing, (
        f"{doc_path.name} is missing the following token-binding "
        f"terms from ``SALIENCE_STATE_TOKEN_FIELDS``: {missing}. "
        f"Both pages must name the call-site AND every token field."
    )


def test_apply_salience_docstring_does_not_assert_superseded_signal() -> None:
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
