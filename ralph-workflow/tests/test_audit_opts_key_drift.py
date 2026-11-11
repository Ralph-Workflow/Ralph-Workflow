from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ralph.testing import audit_opts_key_drift

pytestmark = pytest.mark.subprocess_e2e


def _violations(**modules: str) -> list[audit_opts_key_drift.Violation]:
    index = {
        name: audit_opts_key_drift._index_module(name, ast.parse(source))
        for name, source in modules.items()
    }
    return audit_opts_key_drift.audit_index(index)


def _keywords(**modules: str) -> list[str]:
    return sorted(item.keyword for item in _violations(**modules))


def test_flags_a_keyword_the_callee_reads_under_another_name() -> None:
    modules = {
        "callee": 'def inner(**opts):\n    return opts.get("_monitor_stop_cb")\n',
        "caller": "from callee import inner\n\ndef outer(cb):\n    return inner(monitor_stop_cb=cb)\n",
    }

    violations = _violations(**modules)

    assert [item.keyword for item in violations] == ["monitor_stop_cb"]
    assert violations[0].callee == "callee.inner"
    assert "silently discarded" in str(violations[0])


def test_allows_the_name_the_callee_actually_reads() -> None:
    modules = {
        "callee": 'def inner(**opts):\n    return opts.get("_monitor_stop_cb")\n',
        "caller": "from callee import inner\n\ndef outer(cb):\n    return inner(_monitor_stop_cb=cb)\n",
    }

    assert _keywords(**modules) == []


def test_allows_a_keyword_bound_by_a_named_parameter() -> None:
    modules = {
        "callee": 'def inner(effect, **opts):\n    return opts.get("k")\n',
        "caller": "from callee import inner\n\ndef outer(e):\n    return inner(effect=e)\n",
    }

    assert _keywords(**modules) == []


def test_follows_a_key_read_only_after_a_re_forward() -> None:
    modules = {
        "deep": 'def deepest(**opts):\n    return opts.get("wanted")\n',
        "callee": "from deep import deepest\n\ndef inner(**opts):\n    return deepest(**opts)\n",
        "caller": "from callee import inner\n\ndef outer(v):\n    return inner(wanted=v)\n",
    }

    assert _keywords(**modules) == []


def test_follows_the_dict_handed_to_a_helper_as_a_plain_argument() -> None:
    modules = {
        "helper": 'def read(bag):\n    return bag.get("wanted")\n',
        "callee": "from helper import read\n\ndef inner(**opts):\n    return read(opts)\n",
        "caller": "from callee import inner\n\ndef outer(v):\n    return inner(wanted=v)\n",
    }

    assert _keywords(**modules) == []


@pytest.mark.parametrize(
    "body",
    [
        "    return opts[name]",
        "    return opts.get(name)",
        "    return opts.keys()",
        "    opts.update(other)",
        '    return {**opts, "x": 1} if opts.items() else None',
    ],
    ids=["dynamic-subscript", "dynamic-get", "keys", "update", "items"],
)
def test_stays_silent_when_the_callee_reads_opaquely(body: str) -> None:
    modules = {
        "callee": f"def inner(name=None, other=None, **opts):\n{body}\n",
        "caller": "from callee import inner\n\ndef outer(v):\n    return inner(anything=v)\n",
    }

    assert _keywords(**modules) == []


def test_stays_silent_when_a_forward_target_cannot_be_resolved() -> None:
    modules = {
        "callee": "def inner(**opts):\n    return some_unknown_thing(**opts)\n",
        "caller": "from callee import inner\n\ndef outer(v):\n    return inner(anything=v)\n",
    }

    assert _keywords(**modules) == []


def test_stays_silent_for_callees_without_a_catchall() -> None:
    modules = {
        "callee": "def inner(a=None):\n    return a\n",
        "caller": "from callee import inner\n\ndef outer(v):\n    return inner(a=v)\n",
    }

    assert _keywords(**modules) == []


def test_resolves_a_module_attribute_call() -> None:
    modules = {
        "callee": 'def inner(**opts):\n    return opts.get("wanted")\n',
        "caller": "import callee\n\ndef outer(v):\n    return callee.inner(other=v)\n",
    }

    assert _keywords(**modules) == ["other"]


def test_resolves_an_aliased_import() -> None:
    modules = {
        "callee": 'def inner(**opts):\n    return opts.get("wanted")\n',
        "caller": "from callee import inner as run\n\ndef outer(v):\n    return run(other=v)\n",
    }

    assert _keywords(**modules) == ["other"]


def test_survives_a_recursive_forward() -> None:
    modules = {
        "callee": 'def inner(**opts):\n    if opts.get("stop"):\n        return None\n    return inner(**opts)\n',
        "caller": "from callee import inner\n\ndef outer(v):\n    return inner(stop=v)\n",
    }

    assert _keywords(**modules) == []


def test_missing_root_raises_rather_than_scanning_nothing() -> None:
    with pytest.raises(FileNotFoundError):
        audit_opts_key_drift.audit_tree(Path("/nonexistent"), ("ralph",))


def test_main_reports_missing_root() -> None:
    assert audit_opts_key_drift.main(["definitely-not-a-package"]) == 2


def test_live_tree_has_no_silently_dropped_keys(tmp_path: Path) -> None:
    package = tmp_path / "ralph"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(
        'def target(**opts):\n    return opts.get("k")\n\ndef caller(v):\n    return target(k=v)\n',
        encoding="utf-8",
    )

    violations, scanned = audit_opts_key_drift.audit_tree(tmp_path, ("ralph",))

    assert scanned == 2, f"audit indexed nothing under {tmp_path}; root resolution is wrong"
    assert violations == [], "\n".join(str(item) for item in violations)
