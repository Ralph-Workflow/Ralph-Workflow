from __future__ import annotations

import ast
import re
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_PACKAGE_PROJECT_DIR_NAME: Final = "ralph-workflow"

_RETIRED_STEM: Final = "commit_" + "cleanup"
_RETIRED_HYPHEN_STEM: Final = "commit-" + "cleanup"
_RETIRED_REVIEW_PATTERN: Final = re.compile(
    rf"(?i)\b[a-zA-Z0-9_-]*commit[-_]?{'clean' + 'up'}[a-zA-Z0-9_.-]*\b"
)
_RETIRED_REVIEW_RULE: Final = f"legacy {_RETIRED_STEM} identifier reappeared"
_RETIRED_REVIEW_DETAIL: Final = f"'{{token}}' was replaced by 'commit_review'; legacy {_RETIRED_STEM} identifiers must not be used"
DENIAL_ONLY_PRE_RENAME_SENTINELS: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {
        (
            "ralph/pipeline/_checkpoint_retired_phase_error.py",
            _RETIRED_STEM,
        ): (
            "denial-only default for CheckpointRetiredPhaseError.retired_artifact; "
            "names the pre-rename artifact solely to refuse resume of retired checkpoints"
        ),
        (
            "ralph/pipeline/state.py",
            f"development_{_RETIRED_STEM}",
        ): (
            "denial-only retired phase membership for validate_no_retired_phases; "
            "refuses resume of pre-rename development commit-review checkpoints"
        ),
        (
            "ralph/pipeline/state.py",
            f"development_final_{_RETIRED_STEM}",
        ): (
            "denial-only retired phase membership for validate_no_retired_phases; "
            "refuses resume of pre-rename final commit-review checkpoints"
        ),
        (
            "tests/integration/test_pipeline_commit_review_grading_e2e.py",
            _RETIRED_STEM,
        ): "denial-only planted retired artifact type for commit-review grading e2e refusal",
        (
            "tests/policy/test_artifact_contract_entry_validation.py",
            _RETIRED_STEM,
        ): "denial-only planted retired artifact type for artifact-contract entry validation",
        (
            "tests/policy/test_artifact_contract_missing_prompt.py",
            _RETIRED_STEM,
        ): "denial-only planted retired artifact type for missing-prompt contract refusal",
        (
            "tests/policy/test_artifact_type_closed_set.py",
            _RETIRED_STEM,
        ): "denial-only planted stale artifact type for closed-set policy refusal",
        (
            "tests/recovery/test_preflight_validation.py",
            f"development_{_RETIRED_STEM}",
        ): "denial-only planted retired phase for preflight validation refusal",
        (
            "tests/test_artifact_format_docs.py",
            _RETIRED_STEM,
        ): "denial-only planted retired stem for format-doc materialization refusal",
        (
            "tests/test_materialize_format_docs_pruning.py",
            _RETIRED_STEM,
        ): "denial-only planted retired artifact type for format-doc pruning refusal",
        (
            "tests/test_pipeline_resume_renamed_commit_review.py",
            f"development_{_RETIRED_STEM}",
        ): "denial-only planted retired phase for checkpoint resume refusal",
        (
            "tests/test_pipeline_resume_renamed_commit_review.py",
            f"development_final_{_RETIRED_STEM}",
        ): "denial-only planted retired final phase for checkpoint resume refusal",
        (
            "tests/test_pipeline_resume_renamed_commit_review.py",
            _RETIRED_STEM,
        ): "denial-only planted retired artifact type for checkpoint resume refusal",
        (
            "tests/test_pipeline_runner_resume_renamed_phase.py",
            _RETIRED_STEM,
        ): "denial-only planted retired artifact type for runner resume-phase refusal",
        (
            "tests/test_repo_retired_identifier_guard.py",
            _RETIRED_STEM,
        ): (
            "denial-only detector stem for the tracked-inventory retired-identifier guard; "
            "constructed only to refuse live reintroduction"
        ),
        (
            "tests/test_repo_retired_identifier_guard.py",
            _RETIRED_HYPHEN_STEM,
        ): (
            "denial-only hyphenated detector stem for the tracked-inventory "
            "retired-identifier guard; constructed only to refuse live reintroduction"
        ),
    }
)


def _package_key(rel: str) -> str:
    return rel.removeprefix(_PACKAGE_PROJECT_DIR_NAME + "/")


def _constant_string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string_value(node.left)
        right = _constant_string_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


class _WalkCache:
    __slots__ = ("nodes", "tree")

    def __init__(self) -> None:
        self.tree: ast.AST | None = None
        self.nodes: tuple[ast.AST, ...] = ()


_WALK_CACHE = _WalkCache()


def _walked_nodes(tree: ast.AST) -> tuple[ast.AST, ...]:
    if _WALK_CACHE.tree is not tree:
        _WALK_CACHE.nodes = tuple(ast.walk(tree))
        _WALK_CACHE.tree = tree
    return _WALK_CACHE.nodes


def _iter_folded_string_literals(tree: ast.AST) -> Iterator[tuple[str, int]]:
    for node in _walked_nodes(tree):
        if isinstance(node, ast.BinOp):
            folded = _constant_string_value(node)
            if folded is not None:
                yield folded, node.lineno
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value, node.lineno


def _is_denial_only_pre_rename_sentinel(rel: str, token: str) -> bool:
    return (_package_key(rel), token) in DENIAL_ONLY_PRE_RENAME_SENTINELS
