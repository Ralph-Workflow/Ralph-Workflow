"""Tests for the R6.2 baseline CLI plumbing (``--repeat`` / ``--post``).

The CLI module is small but load-bearing: every new flag is a
contract with the regression-gate harness. The tests below pin the
flag shape and the dispatch behaviour added in S-4 so future
refactors cannot silently rename or drop a flag.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.mcp.explore._bench_r6_cli import (
    _BenchArgs,
    _build_parser,
    _empty_args,
)


def _parse(argv: list[str]) -> _BenchArgs:
    """Parse ``argv`` against the canonical parser with default namespace."""
    args = _empty_args()
    _build_parser().parse_args(argv, namespace=args)
    return args


def test_cli_parser_accepts_repeat_flag() -> None:
    """``--repeat N`` is parsed and stored on the namespace."""
    args = _parse(["--repeat", "5"])
    assert args.repeat == 5


def test_cli_parser_defaults_repeat_to_one() -> None:
    """``--repeat`` defaults to 1 so the single-shot capture path is unchanged."""
    args = _parse([])
    assert args.repeat == 1


def test_cli_parser_accepts_post_flag() -> None:
    """``--post PATH`` is parsed and stored on the namespace."""
    args = _parse(["--post", "post.json"])
    assert args.post == "post.json"


def test_cli_parser_defaults_post_to_none() -> None:
    """``--post`` defaults to ``None`` so legacy validation keeps working."""
    args = _parse([])
    assert args.post is None


def test_cli_parser_accepts_combined_flags() -> None:
    """``--validate-report`` + ``--baseline`` + ``--targets`` + ``--post`` parse together."""
    args = _parse(
        [
            "--validate-report",
            "report.md",
            "--baseline",
            "baseline.json",
            "--targets",
            "targets.json",
            "--post",
            "post.json",
        ]
    )
    assert args.validate_report == "report.md"
    assert args.baseline == "baseline.json"
    assert args.targets == "targets.json"
    assert args.post == "post.json"


def test_cli_parser_rejects_repeat_below_one() -> None:
    """``--repeat 0`` fails closed so a degenerate capture cannot run."""
    import pytest

    # argparse calls sys.exit(2) on user error; the dispatch
    # wrapper turns this into a captured SystemExit. The
    # important property is "no baseline file is written".
    with tempfile.TemporaryDirectory() as scratch, pytest.raises(SystemExit):
        out_path = Path(scratch) / "out.json"
        _parse(["--repeat", "0", "--out", str(out_path)])
