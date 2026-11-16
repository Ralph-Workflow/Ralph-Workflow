from __future__ import annotations

from ralph.testing.audit_integration_single_source import audit_modules, selftest

_XY_RULE = "porcelain unmerged XY set has a second definition"
_UNMERGED_MODULE = "ralph/pipeline/reintroduced_second_reader.py"

_SECOND_READER_SOURCES = [
    "entry.xy in {'UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD'}\n",
    ("from ralph.git.hardening import UNMERGED_PORCELAIN_XY\nentry.xy in UNMERGED_PORCELAIN_XY\n"),
    ("from ralph.git.hardening import UNMERGED_PORCELAIN_XY as XY\nentry.xy in XY\n"),
    (
        "LOCAL_XY_CODES = frozenset({'UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD'})\n"
        "entry.xy in LOCAL_XY_CODES\n"
    ),
    "LOCAL_XY_CODES = {'UU', 'AA', 'DD', 'AU', 'UA', 'DU', 'UD'}\n",
]


def test_selftest_passes() -> None:
    assert selftest() == 0


def test_audit_catches_reintroduced_string_literal_second_reader() -> None:
    violations = audit_modules({_UNMERGED_MODULE: _SECOND_READER_SOURCES[0]})
    assert _XY_RULE in [violation.rule for violation in violations]


def test_audit_catches_reintroduced_name_compared_second_reader() -> None:
    violations = audit_modules({_UNMERGED_MODULE: _SECOND_READER_SOURCES[1]})
    assert _XY_RULE in [violation.rule for violation in violations]


def test_audit_catches_aliased_owner_import_second_reader() -> None:
    violations = audit_modules({_UNMERGED_MODULE: _SECOND_READER_SOURCES[2]})
    assert _XY_RULE in [violation.rule for violation in violations]


def test_audit_catches_locally_defined_frozenset_second_reader() -> None:
    violations = audit_modules({_UNMERGED_MODULE: _SECOND_READER_SOURCES[3]})
    assert _XY_RULE in [violation.rule for violation in violations]


def test_audit_catches_set_literal_definition_second_reader() -> None:
    violations = audit_modules({_UNMERGED_MODULE: _SECOND_READER_SOURCES[4]})
    assert _XY_RULE in [violation.rule for violation in violations]
