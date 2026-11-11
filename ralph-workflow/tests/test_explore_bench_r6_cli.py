from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.mcp.explore._bench_r6_cli import (
    _BenchArgs,
    _build_parser,
    _empty_args,
)


def _parse(argv: list[str]) -> _BenchArgs:
    args = _empty_args()
    _build_parser().parse_args(argv, namespace=args)
    return args


def test_cli_parser_accepts_repeat_flag() -> None:
    args = _parse(["--repeat", "5"])
    assert args.repeat == 5


def test_cli_parser_defaults_repeat_to_one() -> None:
    args = _parse([])
    assert args.repeat == 1


def test_cli_parser_accepts_post_flag() -> None:
    args = _parse(["--post", "post.json"])
    assert args.post == "post.json"


def test_cli_parser_defaults_post_to_none() -> None:
    args = _parse([])
    assert args.post is None


def test_cli_parser_accepts_combined_flags() -> None:
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
    import pytest

    with tempfile.TemporaryDirectory() as scratch, pytest.raises(SystemExit):
        out_path = Path(scratch) / "out.json"
        _parse(["--repeat", "0", "--out", str(out_path)])
