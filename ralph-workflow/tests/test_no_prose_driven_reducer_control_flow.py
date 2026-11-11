from __future__ import annotations

import ast
from pathlib import Path

import pytest

CONTROL_FLOW_FILES: tuple[Path, ...] = (
    Path("ralph/pipeline/reducer.py"),
    Path("ralph/pipeline/run_loop.py"),
    Path("ralph/pipeline/runner.py"),
    Path("ralph/pipeline/_integration_action.py"),
)

ALLOWED_SUBSTRING_SITES: dict[str, tuple[int, int]] = {
    "ralph/pipeline/conflict_resolution/attempt_fault.py": (
        91,
        170,
    ),
}


def _scan_for_prose_control_branches(path: Path) -> list[tuple[int, str]]:
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
                    var_name = child.comparators[0].id
                    if var_name in {"reason", "text", "message", "lower", "lowered"}:
                        offenders.append((child.lineno, child.left.value, var_name))
        assert offenders == [], (
            f"Prose-driven control flow is forbidden in {path.name}. "
            f"Offending sites:\n"
            + "\n".join(f"  line {ln}  substring={s!r}  variable={v}" for ln, s, v in offenders)
        )

    def test_planning_obstruction_helper_still_parses(self) -> None:
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
        from ralph.pipeline import run_loop

        text = Path(run_loop.__file__).read_text(encoding="utf-8")
        assert "last_planning_obstruction" in text, (
            "_route_planning_commit no longer consults the typed kind"
        )
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
