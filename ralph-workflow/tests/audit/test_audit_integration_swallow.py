from __future__ import annotations

from ralph.testing.audit_integration_swallow import audit_modules, selftest

_SWALLOW_RULE = "broad handler returns an untyped normal value"


def test_audit_integration_swallow_selftest() -> None:
    assert selftest() == 0


def test_audit_allows_clean_resolution_handlers() -> None:
    clean_modules = {
        "ralph/pipeline/clean_narrow.py": (
            "def resolve_foo():\n"
            "    try:\n"
            "        return 1\n"
            "    except ValueError:\n"
            "        return None\n"
        ),
        "ralph/pipeline/clean_loud.py": (
            "def resolve_bar():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        logger.warning('swallowed')\n"
            "        return None\n"
        ),
        "ralph/pipeline/clean_raise.py": (
            "def resolution_step():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        raise\n"
        ),
    }
    assert not audit_modules(clean_modules)


def test_audit_flags_unclassified_error_returned_as_normal_result() -> None:
    planted = {
        "ralph/pipeline/planted_swallow.py": (
            "def resolve_conflict():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        return None\n"
        ),
    }
    violations = audit_modules(planted)
    assert len(violations) == 1
    assert violations[0].rule == _SWALLOW_RULE
    assert violations[0].path == "ralph/pipeline/planted_swallow.py"
