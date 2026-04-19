"""Tests for the verbose-by-default CLI behavior and --quiet opt-out contract."""

from __future__ import annotations

import pytest

from ralph.cli.main import _resolve_effective_verbosity
from ralph.cli.options import VerbosityOption
from ralph.config.enums import Verbosity
from ralph.pipeline.runner import _normalize_verbosity, _verbosity_rank


def test_quiet_flag_forces_quiet_regardless_of_verbosity() -> None:
    assert (
        _resolve_effective_verbosity(Verbosity.VERBOSE, quiet=True, debug=False) == Verbosity.QUIET
    )


def test_debug_flag_forces_debug_regardless_of_verbosity() -> None:
    assert (
        _resolve_effective_verbosity(Verbosity.VERBOSE, quiet=False, debug=True) == Verbosity.DEBUG
    )


def test_default_verbosity_is_verbose() -> None:
    assert (
        _resolve_effective_verbosity(Verbosity.VERBOSE, quiet=False, debug=False)
        == Verbosity.VERBOSE
    )


def test_legacy_normal_is_mapped_to_verbose() -> None:
    assert (
        _resolve_effective_verbosity(Verbosity.NORMAL, quiet=False, debug=False)
        == Verbosity.VERBOSE
    )


def test_quiet_wins_over_debug() -> None:
    # --quiet takes precedence over --debug so silent wrapper scripts
    # remain silent when both are passed.
    assert (
        _resolve_effective_verbosity(Verbosity.VERBOSE, quiet=True, debug=True) == Verbosity.QUIET
    )


def test_explicit_full_is_preserved() -> None:
    assert _resolve_effective_verbosity(Verbosity.FULL, quiet=False, debug=False) == Verbosity.FULL


class _FakeClickContext:
    """Minimal stand-in for click.Context used only for option.process_value."""


def test_verbosity_option_process_value_defaults_to_verbose() -> None:
    option = VerbosityOption(param_decls=["--verbosity"])
    assert option.process_value(None, None) == Verbosity.VERBOSE  # type: ignore[arg-type]


def test_verbosity_option_process_value_accepts_legacy_normal_string() -> None:
    option = VerbosityOption(param_decls=["--verbosity"])
    # "normal" is still valid input — _resolve_effective_verbosity upgrades it.
    assert (
        option.process_value(None, "normal")  # type: ignore[arg-type]
        == Verbosity.NORMAL
    )
    # Default (None) should still be VERBOSE.
    assert option.process_value(None, None) == Verbosity.VERBOSE  # type: ignore[arg-type]


def test_verbosity_option_process_value_accepts_numeric_debug_string() -> None:
    option = VerbosityOption(param_decls=["--verbosity"])
    assert option.process_value(None, "4") == Verbosity.DEBUG  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("quiet", Verbosity.QUIET),
        ("verbose", Verbosity.VERBOSE),
        ("full", Verbosity.FULL),
        ("debug", Verbosity.DEBUG),
    ],
)
def test_verbosity_option_process_value_recognised_strings(value: str, expected: Verbosity) -> None:
    option = VerbosityOption(param_decls=["--verbosity"])
    assert option.process_value(None, value) == expected  # type: ignore[arg-type]


_RANK_QUIET = 0
_RANK_NORMAL = 1
_RANK_VERBOSE = 2
_RANK_FULL = 3
_RANK_DEBUG = 4


def test_verbosity_rank_orders_quiet_below_verbose_below_debug() -> None:
    assert _verbosity_rank(Verbosity.QUIET) == _RANK_QUIET
    assert _verbosity_rank(Verbosity.NORMAL) == _RANK_NORMAL
    assert _verbosity_rank(Verbosity.VERBOSE) == _RANK_VERBOSE
    assert _verbosity_rank(Verbosity.FULL) == _RANK_FULL
    assert _verbosity_rank(Verbosity.DEBUG) == _RANK_DEBUG
    assert _verbosity_rank(Verbosity.QUIET) < _verbosity_rank(Verbosity.VERBOSE)
    assert _verbosity_rank(Verbosity.VERBOSE) < _verbosity_rank(Verbosity.DEBUG)


def test_normalize_verbosity_accepts_enum_int_and_none() -> None:
    assert _normalize_verbosity(Verbosity.QUIET) == Verbosity.QUIET
    assert _normalize_verbosity(Verbosity.DEBUG) == Verbosity.DEBUG
    assert _normalize_verbosity(_RANK_QUIET) == Verbosity.QUIET
    assert _normalize_verbosity(_RANK_VERBOSE) == Verbosity.VERBOSE
    assert _normalize_verbosity(_RANK_DEBUG) == Verbosity.DEBUG
    assert _normalize_verbosity(None) == Verbosity.VERBOSE
