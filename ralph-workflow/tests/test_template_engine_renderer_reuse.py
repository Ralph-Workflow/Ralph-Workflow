from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from ralph.prompts.template_engine import render_template

_PARTIALS = {"greeting": "Hello {{ name }}"}
_TEMPLATE = "{{ name }}: {{ items | split_items | join('|') }}"
_INCLUDING_TEMPLATE = "{% include 'greeting.j2' %}"


def _render(name: str, items: str) -> str:
    return render_template(_TEMPLATE, {"name": name, "items": items}, _PARTIALS)


def test_timeout_propagates_instead_of_becoming_template_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.prompts.template_engine import TemplateRenderer

    def raise_timeout(text: str) -> str:
        raise TimeoutError("simulated async timeout mid-render")

    renderer = TemplateRenderer(_PARTIALS)
    monkeypatch.setattr(renderer, "_compiled_template", raise_timeout)

    with pytest.raises(TimeoutError):
        renderer.render(_TEMPLATE, {"name": "alice", "items": "a"})


def test_repeated_renders_are_independent() -> None:
    first = _render("alice", "a, b")
    other = _render("bob", "c, d, e")
    again = _render("alice", "a, b")

    assert first == again, "a reused renderer must not carry state between renders"
    assert first != other


def test_concurrent_renders_do_not_corrupt_each_other() -> None:
    expected = _render("alice", "a, b")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: _render("alice", "a, b"), range(32)))

    assert all(result == expected for result in results)


def test_distinct_partial_sets_stay_separate() -> None:
    variables = {"name": "alice"}
    hello = render_template(_INCLUDING_TEMPLATE, variables, _PARTIALS)
    hi = render_template(_INCLUDING_TEMPLATE, variables, {"greeting": "Hi {{ name }}"})

    assert "Hello alice" in hello
    assert "Hi alice" in hi
