"""Tests for ``ralph.testing.audit_cast_policy``.

The audit is exercised here via fixture source strings rather than the
real tree so the tests stay under 1 second and never depend on the
contents of ``ralph/`` or ``tests/``. Real-tree regression coverage
lives in ``make verify`` (``cast policy audit (audit_cast_policy)``).
"""

from __future__ import annotations

from ralph.testing.audit_cast_policy import (
    _all_leaves_universal,
    _extract_outer_and_params,
    _find_cast_violations,
    _is_sound_dict_widening,
    audit_codebase,
    main,
)

# ---------------------------------------------------------------------------
# _extract_outer_and_params
# ---------------------------------------------------------------------------


class TestExtractOuterAndParams:
    def test_bare_type(self) -> None:
        assert _extract_outer_and_params("str") == ("str", "")

    def test_dict_with_object(self) -> None:
        assert _extract_outer_and_params("dict[str, object]") == ("dict", "str, object")

    def test_list_of_object(self) -> None:
        assert _extract_outer_and_params("list[object]") == ("list", "object")

    def test_nested_generic(self) -> None:
        outer, params = _extract_outer_and_params("dict[str, list[int]]")
        assert outer == "dict"
        assert "str" in params
        assert "list[int]" in params


# ---------------------------------------------------------------------------
# _all_leaves_universal
# ---------------------------------------------------------------------------


class TestAllLeavesUniversal:
    def test_object_is_universal(self) -> None:
        assert _all_leaves_universal("object") is True

    def test_str_is_not_universal(self) -> None:
        assert _all_leaves_universal("str") is False

    def test_list_object_is_universal(self) -> None:
        assert _all_leaves_universal("list[object]") is True

    def test_dict_str_object_not_all_universal(self) -> None:
        # ``str`` is not universal, only ``object`` is.
        assert _all_leaves_universal("dict[str, object]") is False

    def test_dict_str_str_is_not_universal(self) -> None:
        assert _all_leaves_universal("dict[str, str]") is False

    def test_optional_str_not_universal(self) -> None:
        assert _all_leaves_universal("str | None") is False


# ---------------------------------------------------------------------------
# _is_sound_dict_widening
# ---------------------------------------------------------------------------


class TestIsSoundDictWidening:
    def test_dict_str_object_is_sound(self) -> None:
        assert _is_sound_dict_widening("dict[str, object]") is True

    def test_list_object_is_sound(self) -> None:
        assert _is_sound_dict_widening("list[object]") is True

    def test_dict_str_str_not_sound(self) -> None:
        assert _is_sound_dict_widening("dict[str, str]") is False

    def test_bare_str_not_sound(self) -> None:
        assert _is_sound_dict_widening("str") is False


# ---------------------------------------------------------------------------
# _find_cast_violations
# ---------------------------------------------------------------------------


def _lines(*raw_lines: str) -> list[str]:
    return list(raw_lines)


class TestFindCastViolationsInProduction:
    def test_no_cast_means_no_violation(self) -> None:
        violations = _find_cast_violations(
            _lines("x = 1", "y = 'hello'"),
            "ralph/sample.py",
        )
        assert violations == []

    def test_universal_object_cast_is_sound(self) -> None:
        violations = _find_cast_violations(
            _lines("x = value"),
            "ralph/sample.py",
        )
        assert violations == []

    def test_dict_str_object_is_sound_by_construction(self) -> None:
        violations = _find_cast_violations(
            _lines("x = must_mapping(value)"),
            "ralph/sample.py",
        )
        assert violations == []

    def test_list_object_is_sound_by_construction(self) -> None:
        violations = _find_cast_violations(
            _lines("x = value"),
            "ralph/sample.py",
        )
        assert violations == []

    def test_cast_to_str_over_external_data_is_violation(self) -> None:
        violations = _find_cast_violations(
            _lines('x = cast("str", value)'),
            "ralph/sample.py",
        )
        assert len(violations) == 1
        assert violations[0].category == "forbidden-cast"

    def test_cast_inside_triple_quoted_string_is_ignored(self) -> None:
        violations = _find_cast_violations(
            _lines(
                'doc = """',
                '    cast("str", value)',
                '"""',
            ),
            "ralph/sample.py",
        )
        assert violations == []

    def test_seam_marker_disables_violation(self) -> None:
        lines = _lines(
            'x = cast("str", value)  # cast-policy: seam: runtime proof',
        )
        violations = _find_cast_violations(lines, "ralph/sample.py")
        assert violations == []


class TestFindCastViolationsInTests:
    def test_any_cast_in_test_file_is_violation(self) -> None:
        violations = _find_cast_violations(
            _lines('x = cast("str", value)'),
            "tests/test_sample.py",
        )
        assert len(violations) == 1
        assert violations[0].category == "test-cast"

    def test_dict_str_object_still_violates_in_test(self) -> None:
        violations = _find_cast_violations(
            _lines('x = cast("dict[str, object]", value)'),
            "tests/test_sample.py",
        )
        assert len(violations) == 1
        assert violations[0].category == "test-cast"

    def test_seam_marker_does_not_exempt_test(self) -> None:
        violations = _find_cast_violations(
            _lines('x = cast("str", value)  # cast-policy: seam: nope'),
            "tests/test_sample.py",
        )
        assert len(violations) == 1
        assert violations[0].category == "test-cast"


class TestAuditFileExemptions:
    def test_audit_itself_is_exempt(self) -> None:
        violations = _find_cast_violations(
            _lines('x = cast("str", value)'),
            "ralph/testing/audit_cast_policy.py",
        )
        assert violations == []

    def test_audit_test_itself_is_exempt(self) -> None:
        violations = _find_cast_violations(
            _lines('x = cast("str", value)'),
            "tests/test_audit_cast_policy.py",
        )
        assert violations == []


# ---------------------------------------------------------------------------
# audit_codebase / main
# ---------------------------------------------------------------------------


class TestAuditCodebase:
    def test_clean_fixture_root_has_no_violations(self, tmp_path) -> None:
        clean = tmp_path / "ralph"
        clean.mkdir()
        (clean / "ok.py").write_text("x = 1\n")
        (tmp_path / "tests").mkdir()
        violations, _ = audit_codebase(tmp_path)
        assert violations == []

    def test_test_fixture_is_flagged(self, tmp_path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_x.py").write_text('x = cast("str", value)\n')
        violations, _ = audit_codebase(tmp_path)
        assert any(v.category == "test-cast" for v in violations)


class TestMain:
    def test_clean_root_exits_zero(self, tmp_path, capsys) -> None:
        rc = main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "No cast-policy violations" in captured.out
        assert rc == 0

    def test_missing_directory_exits_two(self, capsys) -> None:
        rc = main(["/nonexistent/path/for/audit"])
        assert rc == 2

    def test_violations_exit_one(self, tmp_path, capsys) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_x.py").write_text('x = cast("str", value)\n')
        rc = main([str(tmp_path)])
        captured = capsys.readouterr()
        assert "CAST POLICY VIOLATIONS FOUND" in captured.out
        assert rc == 1


# ---------------------------------------------------------------------------
# Boundary helper coverage
# ---------------------------------------------------------------------------


def test_inside_parser_parse_event_is_boundary_exempt(tmp_path) -> None:
    """Casts inside ralph/agents/parsers/*_parse_event helpers are exempt.

    These helpers are registered in the policy's
    ``sanctioned_dynamic_boundaries`` and the audit's
    ``_BOUNDARY_HELPERS`` registry.
    """
    src = tmp_path / "ralph"
    (src / "agents" / "parsers").mkdir(parents=True)
    (src / "agents" / "parsers" / "claude.py").write_text(
        "def parse_event(line: str) -> dict:\n"
        "    data = must_mapping(json.loads(line))\n"
        "    return data\n"
    )
    violations, _ = audit_codebase(tmp_path)
    assert violations == []
