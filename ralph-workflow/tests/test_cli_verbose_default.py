"""Tests for the verbose-by-default CLI behavior and --quiet opt-out contract."""

from __future__ import annotations

from ralph.cli import options as options_module
from ralph.cli.main import resolve_effective_verbosity
from ralph.config.enums import Verbosity
from ralph.pipeline.phase_rendering import normalize_verbosity, verbosity_rank


def test_quiet_flag_forces_quiet_regardless_of_verbosity() -> None:
    assert (
        resolve_effective_verbosity(Verbosity.VERBOSE, quiet=True, debug=False) == Verbosity.QUIET
    )


def test_debug_flag_forces_debug_regardless_of_verbosity() -> None:
    assert (
        resolve_effective_verbosity(Verbosity.VERBOSE, quiet=False, debug=True) == Verbosity.DEBUG
    )


def test_default_verbosity_is_verbose() -> None:
    assert (
        resolve_effective_verbosity(Verbosity.VERBOSE, quiet=False, debug=False)
        == Verbosity.VERBOSE
    )


def test_legacy_normal_is_mapped_to_verbose() -> None:
    assert (
        resolve_effective_verbosity(Verbosity.NORMAL, quiet=False, debug=False) == Verbosity.VERBOSE
    )


def test_quiet_wins_over_debug() -> None:
    # --quiet takes precedence over --debug so silent wrapper scripts
    # remain silent when both are passed.
    assert resolve_effective_verbosity(Verbosity.VERBOSE, quiet=True, debug=True) == Verbosity.QUIET


def test_explicit_full_is_preserved() -> None:
    assert resolve_effective_verbosity(Verbosity.FULL, quiet=False, debug=False) == Verbosity.FULL


def test_dead_verbosity_option_class_is_not_part_of_cli_surface() -> None:
    """Cleanup should remove the unused custom option class from the CLI module."""
    assert not hasattr(options_module, "VerbosityOption")


_RANK_QUIET = 0
_RANK_NORMAL = 1
_RANK_VERBOSE = 2
_RANK_FULL = 3
_RANK_DEBUG = 4


def test_verbosity_rank_orders_quiet_below_verbose_below_debug() -> None:
    assert verbosity_rank(Verbosity.QUIET) == _RANK_QUIET
    assert verbosity_rank(Verbosity.NORMAL) == _RANK_NORMAL
    assert verbosity_rank(Verbosity.VERBOSE) == _RANK_VERBOSE
    assert verbosity_rank(Verbosity.FULL) == _RANK_FULL
    assert verbosity_rank(Verbosity.DEBUG) == _RANK_DEBUG
    assert verbosity_rank(Verbosity.QUIET) < verbosity_rank(Verbosity.VERBOSE)
    assert verbosity_rank(Verbosity.VERBOSE) < verbosity_rank(Verbosity.DEBUG)


def test_normalize_verbosity_accepts_enum_int_and_none() -> None:
    assert normalize_verbosity(Verbosity.QUIET) == Verbosity.QUIET
    assert normalize_verbosity(Verbosity.DEBUG) == Verbosity.DEBUG
    assert normalize_verbosity(_RANK_QUIET) == Verbosity.QUIET
    assert normalize_verbosity(_RANK_VERBOSE) == Verbosity.VERBOSE
    assert normalize_verbosity(_RANK_DEBUG) == Verbosity.DEBUG
    assert normalize_verbosity(None) == Verbosity.VERBOSE
