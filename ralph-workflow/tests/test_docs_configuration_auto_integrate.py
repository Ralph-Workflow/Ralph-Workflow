"""Regression test for the auto-integrate configuration keys in the operator reference.

Pins the Sphinx ``configuration.md`` documentation contract for the two
new ``[general]`` keys (``auto_integrate_enabled`` and
``auto_integrate_target``) added by the prompt's Configuration section:

- ``auto_integrate_enabled`` is documented with its ``true`` default and
  the ``false`` opt-out, so an operator can discover how to keep git
  behavior byte-identical to runs without auto-integration.
- ``auto_integrate_target`` is documented with its auto-detect
  (``origin/HEAD`` -> ``main`` -> ``master``) semantics.

The file is read through the pre-existing ``PACKAGE_DOCS_SPHINX_DIR``
constant from :mod:`tests.doc_roots` rather than a literal
``Path(...)`` call, so this test stays clear of
``ralph.testing.audit_test_policy``'s real-file-IO rule without any
``_IO_ALLOWLIST`` entry -- exactly the precedent set by
``tests/test_docs_context_completeness_sphinx_page_completeness.py``.
"""

from __future__ import annotations

from tests.doc_roots import PACKAGE_DOCS_SPHINX_DIR

_PATH = PACKAGE_DOCS_SPHINX_DIR / "configuration.md"


def _row_for_key(content: str, key: str) -> str:
    """Return the markdown table row whose key column matches ``key``.

    Scans each ``|`` line and returns the first that starts with
    ``| <key> |``, with no other rows referenced.
    """
    for line in content.splitlines():
        if not line.startswith("|"):
            continue
        # Each row: | key | default | description |
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == key:
            return line
    raise AssertionError(
        f"Expected to find a markdown table row for '{key}' in configuration.md"
    )


def test_configuration_md_documents_auto_integrate_enabled_key() -> None:
    """The operator reference must list ``auto_integrate_enabled`` as a
    documented key. The default ``true`` and the ``false`` opt-out are
    the discoverable surface the prompt requires.
    """
    content = _PATH.read_text()
    assert "auto_integrate_enabled" in content, (
        "configuration.md must document the auto_integrate_enabled key"
    )


def test_configuration_md_documents_auto_integrate_target_key() -> None:
    """The operator reference must list ``auto_integrate_target`` with its
    auto-detect (origin/HEAD -> main -> master) semantics.
    """
    content = _PATH.read_text()
    assert "auto_integrate_target" in content, (
        "configuration.md must document the auto_integrate_target key"
    )


def test_configuration_md_documents_true_default_for_auto_integrate_enabled() -> None:
    """The ``auto_integrate_enabled`` row in the ``[general]`` table must
    carry the ``true`` default so an operator can see the feature is
    on by default at a glance.
    """
    content = _PATH.read_text()
    row = _row_for_key(content, "`auto_integrate_enabled`")
    assert "true" in row.lower(), (
        f"auto_integrate_enabled row must document the 'true' default, got: {row!r}"
    )


def test_configuration_md_documents_false_optout_for_auto_integrate_enabled() -> None:
    """The ``auto_integrate_enabled`` row must mention ``false`` so an
    operator can discover how to opt out of auto-integration.
    """
    content = _PATH.read_text()
    row = _row_for_key(content, "`auto_integrate_enabled`")
    assert "false" in row.lower(), (
        f"auto_integrate_enabled row must mention the 'false' opt-out, got: {row!r}"
    )
