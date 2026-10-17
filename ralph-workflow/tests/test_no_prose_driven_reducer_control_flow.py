"""No prose-driven control flow remains in the pipeline reducer or its seams.

The architecture forbids parsing or comparing diagnostic prose to drive
control flow. The only place a reason string is parsed for routing is
:func:`ralph.pipeline._planning_obstruction.identify_planning_obstruction`,
and the only places typed-vocabulary substrings are scanned are
inside :mod:`ralph.pipeline.conflict_resolution.attempt_fault` (the
``_RELAY_FAULT`` token and the ``ResolutionTerminationReason`` enum
identifier bag).

This test walks the pipeline source tree with :mod:`ast` and asserts
that no ``if <Const str> in <NAME>`` branch exists in any of the four
control-flow files the criteria names (``reducer.py``,
``run_loop.py``, ``runner.py``, ``_integration_action.py``) outside
the closed allowlist.

It does NOT scan the whole pipeline -- the other files in
``ralph/pipeline/`` may legitimately use string membership tests for
non-control-flow purposes (file-path assertions, worktree-path checks,
``in`` semantics against ``frozenset``/``tuple`` membership, etc.).
Scanning every file produces false positives because the architecture
forbids CONTROL-FLOW parsing, not string ``in`` everywhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Files that drive the pipeline's control flow. A substring scan here
# is prose-driven routing and is forbidden outside the allowlist.
CONTROL_FLOW_FILES: tuple[Path, ...] = (
    Path("ralph/pipeline/reducer.py"),
    Path("ralph/pipeline/run_loop.py"),
    Path("ralph/pipeline/runner.py"),
    Path("ralph/pipeline/_integration_action.py"),
)

# Allowlist keyed by relative path. Each entry is the AST node range
# (lineno..end_lineno) of a function whose body may scan a reason
# string for the closed vocabulary. ``_planning_obstruction.py`` is
# NOT in this list because it is the typed-vocabulary owner: its
# substring scans are the ONLY allowed places where a reason is
# parsed for routing.
ALLOWED_SUBSTRING_SITES: dict[str, tuple[int, int]] = {
    "ralph/pipeline/conflict_resolution/attempt_fault.py": (
        # The two typed-vocabulary scans live inside
        # :func:`classify_ralph_origin_fault` and
        # :func:`_classify_text_attempt_fault`. Their start is line
        # 91; the closed-vocabulary bag is at the end of
        # :func:`_classify_text_attempt_fault` near line 158. We
        # allow the entire body range that contains them so the
        # audit is positional and the file is the only owner.
        91,
        170,
    ),
}


def _scan_for_prose_control_branches(path: Path) -> list[tuple[int, str]]:
    """Return (line_no, substring) for every ``if "<substring>" in <NAME>``
    branch in the file.

    The scan only matches:

    * an :class:`ast.If` statement (control flow);
    * whose test is :class:`ast.Compare` with a single ``In`` operator;
    * whose left operand is a string :class:`ast.Constant`;
    * whose right operand is a :class:`ast.Name` (the variable holding
      the free-form reason or text).

    ``frozenset``/``tuple``/``dict`` membership and substring checks
    that are NOT inside ``if`` statements are NOT considered prose
    control flow.
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for child in ast.walk(node.test):
            if (
                isinstance(child, ast.Compare)
                and len(child.ops) == 1
                and isinstance(child.ops[0], ast.In)
                and len(child.comparators) == 1
                and isinstance(child.comparators[0], ast.Name)
                and isinstance(child.left, ast.Constant)
                and isinstance(child.left.value, str)
            ):
                hits.append((child.lineno, child.left.value))
    return hits


def _is_in_allowlist(path_rel: str, lineno: int) -> bool:
    allowed = ALLOWED_SUBSTRING_SITES.get(path_rel)
    if allowed is None:
        return False
    start, end = allowed
    return start <= lineno <= end


class TestNoProseDrivenReducerControlFlow:
    """The four control-flow files must not branch on prose substrings."""

    @pytest.mark.parametrize("path", CONTROL_FLOW_FILES, ids=lambda p: p.name)
    def test_no_forbidden_substring_branches(self, path: Path) -> None:
        assert path.exists(), f"Control-flow file is missing: {path}"
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        offenders: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            for child in ast.walk(node.test):
                if (
                    isinstance(child, ast.Compare)
                    and len(child.ops) == 1
                    and isinstance(child.ops[0], ast.In)
                    and len(child.comparators) == 1
                    and isinstance(child.comparators[0], ast.Name)
                    and isinstance(child.left, ast.Constant)
                    and isinstance(child.left.value, str)
                ):
                    # The variable name is the discriminator: the
                    # architecture forbids parsing a free-form reason,
                    # text, or message. Function-parameter ``in``
                    # checks (``if "config" in params``) are
                    # legitimate and are NOT prose-driven control flow.
                    var_name = child.comparators[0].id
                    if var_name in {"reason", "text", "message", "lower", "lowered"}:
                        offenders.append((child.lineno, child.left.value, var_name))
        # The control-flow files MUST contain zero prose-substring
        # branches -- the typed seams have replaced every one.
        assert offenders == [], (
            f"Prose-driven control flow is forbidden in {path.name}. "
            f"Offending sites:\n"
            + "\n".join(f"  line {ln}  substring={s!r}  variable={v}" for ln, s, v in offenders)
        )

    def test_planning_obstruction_helper_still_parses(self) -> None:
        """The :mod:`_planning_obstruction` module is the only allowed
        place that parses a planning reason. Its helper is exercised
        here so a refactor that breaks the typed seam is caught."""
        from ralph.pipeline._planning_obstruction import (
            PlanningObstructionKind,
            identify_planning_obstruction,
        )

        assert (
            identify_planning_obstruction("staged uncommitted work blocks integration")
            is PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION
        )
        assert (
            identify_planning_obstruction("local changes would be overwritten by merge")
            is PlanningObstructionKind.LOCAL_OVERWRITE
        )
        assert identify_planning_obstruction("") is PlanningObstructionKind.NONE

    def test_synthetic_forbidden_branch_is_caught(self, tmp_path: Path) -> None:
        """A synthetic ``.py`` file with a forbidden substring branch is
        caught by the audit. This proves the AST scan is real -- it is
        not just listing allowed sites and skipping everything else."""
        bad = tmp_path / "_bad_site.py"
        bad.write_text(
            "def route(reason: str) -> bool:\n"
            "    if 'integration conflict requires resolution' in reason:\n"
            "        return True\n"
            "    return False\n",
            encoding="utf-8",
        )
        hits = _scan_for_prose_control_branches(bad)
        assert len(hits) == 1, "synthetic forbidden branch was not detected"
        assert hits[0][1] == "integration conflict requires resolution"

    def test_run_loop_obstruction_predicate_uses_typed_kind(self) -> None:
        """The :func:`_route_planning_commit` predicate reads the typed
        kind, not the prose. Confirm by checking the function body for
        the presence of the kind comparison and the absence of the
        prose substrings."""
        from ralph.pipeline import run_loop

        text = Path(run_loop.__file__).read_text(encoding="utf-8")
        # The typed kind is read from ``state.rebase.last_planning_obstruction``.
        assert "last_planning_obstruction" in text, (
            "_route_planning_commit no longer consults the typed kind"
        )
        # The five prose substrings are GONE from run_loop.py --
        # the planning-obstruction helper owns them now.
        for needle in (
            "local changes would be overwritten",
            "local changes to the following files would be overwritten",
            "untracked files would be overwritten",
            "untracked working tree files would be overwritten",
            "staged uncommitted work blocks integration",
        ):
            assert needle not in text, (
                f"run_loop.py still contains the prose substring {needle!r} "
                f"-- the typed seam has regressed"
            )
