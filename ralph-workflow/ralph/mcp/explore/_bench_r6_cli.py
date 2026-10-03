"""Module-level CLI for the R6.2 baseline harness.

Split from :mod:`ralph.mcp.explore._bench_r6_metrics` so the hub module
stays under the repository file-size cap (the 1000-line audit limit
in :mod:`ralph.testing.audit_repo_structure`). Re-exports the public
``main`` entry point and keeps the argparse plumbing in one place so
operators running ``python -m ralph.mcp.explore._bench_r6_metrics``
get every R6.2 capture / validate flag (capture / validation / cross-
check) from a small, dedicated module.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import Final

from ralph.mcp.explore._bench_r6_metrics import (
    _R6_3_WORKLOADS,
    _WORKLOAD_ALIASES,
    run_capture_baseline,
    run_validate_baseline,
    run_validate_report,
)
from ralph.process._spawn_env import sanitize_process_environment


class _BenchArgs(argparse.Namespace):
    """Typed argparse namespace for the R6.2 baseline CLI."""

    workloads: str
    out: str | None
    capture_baseline: str | None
    validate_baseline: str | None
    validate_report: str | None
    baseline: str | None
    targets: str | None
    list_workloads: bool


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser; shared between ``main`` and the test."""
    parser = argparse.ArgumentParser(
        prog="python -m ralph.mcp.explore._bench_r6_metrics",
        description=(
            "Capture and validate the indexed-explore R6.2 baseline "
            "and before/after report (wt-11 S-8/S-9/S-10)."
        ),
    )
    parser.add_argument(
        "--workloads",
        metavar="LIST",
        default=",".join(_R6_3_WORKLOADS),
        help=(
            "Comma-separated list of R6.3 workload names to capture. "
            f"Default: {','.join(_R6_3_WORKLOADS)}"
        ),
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help=(
            "Capture the baseline into PATH. Falls back to "
            "--capture-baseline when --capture-baseline is also given."
        ),
    )
    parser.add_argument(
        "--capture-baseline",
        metavar="PATH",
        default=None,
        help=(
            "Capture the baseline into PATH (alias for --out)."
        ),
    )
    parser.add_argument(
        "--validate-baseline",
        metavar="PATH",
        default=None,
        help="Validate the canonical R6.2 baseline at PATH.",
    )
    parser.add_argument(
        "--validate-report",
        metavar="PATH",
        default=None,
        help=(
            "Validate the S-10 before/after report at PATH. "
            "When supplied, --baseline and --targets MUST also be "
            "given so the report rows are mechanically cross-checked "
            "against the committed baseline and targets JSON files."
        ),
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        default=None,
        help=(
            "Companion baseline JSON for --validate-report. The "
            "report's Baseline cells must equal the JSON values."
        ),
    )
    parser.add_argument(
        "--targets",
        metavar="PATH",
        default=None,
        help=(
            "Companion targets JSON for --validate-report. The "
            "report's Target cells must equal the JSON values."
        ),
    )
    parser.add_argument(
        "--list-workloads",
        action="store_true",
        help="Print the R6.3 workload names and exit.",
    )
    return parser


_DEFAULT_ARGS: Final[tuple[tuple[str, object], ...]] = (
    ("workloads", ",".join(_R6_3_WORKLOADS)),
    ("out", None),
    ("capture_baseline", None),
    ("validate_baseline", None),
    ("validate_report", None),
    ("baseline", None),
    ("targets", None),
    ("list_workloads", False),
)


def _empty_args() -> _BenchArgs:
    """Return a fully-defaulted ``_BenchArgs`` namespace."""
    args = _BenchArgs()
    for field, value in _DEFAULT_ARGS:
        setattr(args, field, value)
    return args


def _dispatch_validate_report(args: _BenchArgs) -> int:
    """Dispatch ``--validate-report`` with mandatory companion gates."""
    if (args.baseline is None) != (args.targets is None):
        print(
            "FAIL: --baseline and --targets must be supplied together",
            file=sys.stderr,
        )
        return 1
    assert args.validate_report is not None
    return run_validate_report(
        args.validate_report,
        baseline_path=args.baseline,
        targets_path=args.targets,
    )


def _dispatch_capture(parser: argparse.ArgumentParser, args: _BenchArgs) -> int:
    """Dispatch capture / capture-list / workload-validation paths."""
    requested = [
        _WORKLOAD_ALIASES.get(w.strip(), w.strip())
        for w in args.workloads.split(",")
        if w.strip()
    ]
    unknown = tuple(w for w in requested if w not in _R6_3_WORKLOADS)
    if unknown:
        parser.error(
            f"unknown workload name(s) {list(unknown)!r}; "
            f"valid names: {list(_R6_3_WORKLOADS)}"
        )
    out_path = args.out or args.capture_baseline
    if out_path is None:
        parser.error(
            "one of --out PATH, --capture-baseline PATH, "
            "--validate-baseline PATH, --validate-report PATH is required"
        )
    print(
        f"capture workloads={requested!r} out={out_path!r}",
        file=sys.stderr,
        flush=True,
    )
    return run_capture_baseline(out_path, workloads=requested)


def main(argv: Sequence[str] | None = None) -> int:
    """Module-level CLI for ``python -m ralph.mcp.explore._bench_r6_metrics``.

    S-8 (wt-11) wires the missing CLI surface so the verify command

        python -m ralph.mcp.explore._bench_r6_metrics \\
            --workloads small,ralph_self,large_synthetic,multi_session \\
            --out docs/performance/explore-index-baseline-pre.json

    captures the pre-change baseline. ``--workloads`` filters by
    R6.3 workload name (default: all four) and ``--out`` writes
    the canonical baseline JSON. ``--validate-baseline`` and
    ``--validate-report`` (with mandatory ``--baseline`` and
    ``--targets`` companions) keep the post-capture validators
    available from this same module.
    """
    sanitize_process_environment()
    parser = _build_parser()
    args = _empty_args()
    parser.parse_args(list(argv) if argv is not None else None, namespace=args)
    if args.list_workloads:
        for name in _R6_3_WORKLOADS:
            print(name)
        return 0
    if args.validate_report is not None:
        return _dispatch_validate_report(args)
    if args.validate_baseline is not None:
        return run_validate_baseline(args.validate_baseline)
    return _dispatch_capture(parser, args)


__all__ = ["_BenchArgs", "main"]
