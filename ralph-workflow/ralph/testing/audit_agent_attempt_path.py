from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "ARTIFACTLESS_ATTEMPT_EXEMPTIONS",
    "ARTIFACTLESS_ATTEMPT_RULE",
    "CHAIN_RETRY_RULE",
    "RAW_AGENT_INVOKE_RULE",
    "RAW_AGENT_INVOKE_SEAMS",
    "SELECTION_CALLS",
    "SELECTION_OWNERS",
    "SELECTION_RULE",
    "Violation",
    "audit_modules",
    "audit_tree",
    "main",
    "selftest",
]

SHARED_ATTEMPT_PATH: Final = "ralph/pipeline/attempt_runner.py"

SELECTION_RULE: Final = "agent_selection_outside_shared_attempt_path"
RAW_AGENT_INVOKE_RULE: Final = "raw_agent_invoke_outside_shared_attempt_path"
ARTIFACTLESS_ATTEMPT_RULE: Final = "artifactless_attempt_without_progress_check"
CHAIN_RETRY_RULE: Final = "chain_attempt_retry_outside_shared_attempt_path"
_CHAIN_ATTEMPT_NAME: Final = "run_chain_attempt"

SELECTION_CALLS: Final[frozenset[str]] = frozenset(
    {"preferred_agent_index", "earliest_available_wait_ms", "earliest_eligible_wait_ms"}
)

SELECTION_OWNERS: Final[Mapping[str, str]] = {
    "ralph/recovery/controller.py": "the cooldown owner",
    SHARED_ATTEMPT_PATH: "run_chain_attempt, the shared sub-pipeline attempt path",
    "ralph/pipeline/reducer.py": "main phase loop: selection is reducer state",
    "ralph/pipeline/orchestrator.py": "main phase loop: selection is reducer state",
    "ralph/pipeline/run_loop.py": "main phase loop: cooldown waits are pipeline states",
    "ralph/pipeline/effect_router.py": "main phase loop: selection is reducer state",
    "ralph/pipeline/conflict_resolution/session.py": "conflict rounds: per-round cohort",
    "ralph/pipeline/conflict_resolution/resolver_health.py": "conflict rounds: per-round cohort",
    "ralph/pipeline/conflict_resolution/_driver_rounds.py": "conflict rounds: per-round cohort",
    "ralph/pipeline/conflict_resolution/round_verdict.py": "conflict rounds: per-round cohort",
    "ralph/pipeline/conflict_resolution/driver.py": "conflict rounds: per-round cohort",
    "ralph/pipeline/plumbing/commit_plumbing.py": "commit chain: per-agent prompt rendering",
}

RAW_AGENT_INVOKE_SEAMS: Final[Mapping[str, str]] = {
    "ralph/pipeline/effect_executor.py": "the shared effect runner behind run_agent_attempt",
    "ralph/_session_runtime_deps.py": "raw invoke seam wrapped by the SESSION_RUNTIME runner",
    "ralph/recovery/canary_probe.py": "raw invoke seam wrapped by the CANARY_PROBE runner",
    "ralph/agents/__init__.py": "package re-export, no call",
    "ralph/cli/commands/commit.py": "lazy re-export, no call",
}

ARTIFACTLESS_ATTEMPT_EXEMPTIONS: Final[Mapping[str, str]] = {
    "ralph/recovery/canary_probe.py": "a canary probe's reply is its own verdict",
    "ralph/pipeline/conflict_resolution/session.py": (
        "unresolved conflicted paths are classified by ConflictAttemptOutcome"
    ),
}

_INVOKE_MODULE: Final = "ralph.agents.invoke"
_RAW_INVOKE_NAME: Final = "invoke_agent"
_EFFECT_CLASS: Final = "InvokeAgentEffect"
_EVIDENCE_KEYWORD: Final = "requires_completion_evidence"
_PROGRESS_KEYWORD: Final = "progress_check"
_TRIGGER_TOKENS: Final[tuple[str, ...]] = (
    *SELECTION_CALLS,
    _RAW_INVOKE_NAME,
    _EVIDENCE_KEYWORD,
    _CHAIN_ATTEMPT_NAME,
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.detail}"


def _call_attr(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _invoke_module_aliases(tree: ast.Module) -> frozenset[str]:
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(
                alias.asname or alias.name for alias in node.names if alias.name == _INVOKE_MODULE
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "ralph.agents":
            aliases.update(
                alias.asname or "invoke" for alias in node.names if alias.name == "invoke"
            )
    return frozenset(aliases)


def _selection_violations(rel_path: str, tree: ast.Module) -> Iterator[Violation]:
    if rel_path in SELECTION_OWNERS:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            if name in SELECTION_CALLS:
                yield Violation(
                    rel_path,
                    node.lineno,
                    SELECTION_RULE,
                    f"'{name}' selects or waits for an agent outside the shared attempt path; "
                    f"call run_chain_attempt in {SHARED_ATTEMPT_PATH}",
                )


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Call):
            func = inner.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def _chain_attempt_callers(tree: ast.Module) -> set[str]:
    reaching = {_CHAIN_ATTEMPT_NAME}
    functions = [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    changed = True
    while changed:
        changed = False
        for function in functions:
            if function.name not in reaching and _called_names(function) & reaching:
                reaching.add(function.name)
                changed = True
    return reaching


def _chain_retry_violations(rel_path: str, tree: ast.Module) -> Iterator[Violation]:
    if rel_path == SHARED_ATTEMPT_PATH:
        return
    reaching = _chain_attempt_callers(tree)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            continue
        looped = _called_names(node) & reaching
        if looped:
            yield Violation(
                rel_path,
                node.lineno,
                CHAIN_RETRY_RULE,
                f"a loop re-runs {sorted(looped)[0]!r}, which reaches {_CHAIN_ATTEMPT_NAME}; "
                f"retry, cooldown failover and give-up belong to {_CHAIN_ATTEMPT_NAME} in "
                f"{SHARED_ATTEMPT_PATH}, so call it once and use its result",
            )


def _raw_invoke_violations(rel_path: str, tree: ast.Module) -> Iterator[Violation]:
    if rel_path in RAW_AGENT_INVOKE_SEAMS:
        return
    aliases = _invoke_module_aliases(tree)
    detail = (
        f"{_INVOKE_MODULE}.{_RAW_INVOKE_NAME} launches an agent with no attempt finalization; "
        f"use run_chain_attempt or run_agent_attempt in {SHARED_ATTEMPT_PATH}"
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _INVOKE_MODULE:
            if any(alias.name == _RAW_INVOKE_NAME for alias in node.names):
                yield Violation(rel_path, node.lineno, RAW_AGENT_INVOKE_RULE, detail)
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == _RAW_INVOKE_NAME
            and isinstance(node.value, ast.Name)
            and node.value.id in aliases
        ):
            yield Violation(rel_path, node.lineno, RAW_AGENT_INVOKE_RULE, detail)


def _is_true_constant(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _passes_progress_check(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.Call)
        and any(keyword.arg == _PROGRESS_KEYWORD for keyword in node.keywords)
        for node in ast.walk(tree)
    )


def _artifactless_violations(rel_path: str, tree: ast.Module) -> Iterator[Violation]:
    if rel_path in ARTIFACTLESS_ATTEMPT_EXEMPTIONS or _passes_progress_check(tree):
        return
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_attr(node) == _EFFECT_CLASS):
            continue
        for keyword in node.keywords:
            if keyword.arg == _EVIDENCE_KEYWORD and not _is_true_constant(keyword.value):
                yield Violation(
                    rel_path,
                    node.lineno,
                    ARTIFACTLESS_ATTEMPT_RULE,
                    "an attempt that may complete without an artifact must pass "
                    f"{_PROGRESS_KEYWORD}= to the shared attempt path, so an attempt that "
                    "changed nothing is finalized as unproductive instead of completed",
                )


def audit_modules(modules: Mapping[str, str]) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path, source in sorted(modules.items()):
        if not any(token in source for token in _TRIGGER_TOKENS):
            continue
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            continue
        violations.extend(_selection_violations(rel_path, tree))
        violations.extend(_raw_invoke_violations(rel_path, tree))
        violations.extend(_artifactless_violations(rel_path, tree))
        violations.extend(_chain_retry_violations(rel_path, tree))
    return violations


def _production_modules(repo_root: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    for path in sorted((repo_root / "ralph").rglob("*.py")):
        rel_path = path.relative_to(repo_root).as_posix()
        if rel_path.startswith("ralph/testing/") or "__pycache__" in path.parts:
            continue
        modules[rel_path] = path.read_text(encoding="utf-8")
    return modules


def _stale_allowlist_violations(modules: Mapping[str, str]) -> list[Violation]:
    stale: list[Violation] = []
    for allowlist in (SELECTION_OWNERS, RAW_AGENT_INVOKE_SEAMS, ARTIFACTLESS_ATTEMPT_EXEMPTIONS):
        stale.extend(
            Violation(rel_path, 0, "stale_allowlist_entry", "allowlisted module does not exist")
            for rel_path in allowlist
            if rel_path not in modules
        )
    return stale


def audit_tree(repo_root: Path) -> list[Violation]:
    modules = _production_modules(repo_root)
    return [*_stale_allowlist_violations(modules), *audit_modules(modules)]


def selftest() -> int:
    clean = {
        "ralph/project_policy/clean.py": (
            "def run(build):\n"
            "    return run_chain_attempt(\n"
            "        'p', build_effect=lambda a: InvokeAgentEffect(\n"
            "            agent_name=a, requires_completion_evidence=False),\n"
            "        progress_check=lambda: True)\n"
        ),
        "ralph/pipeline/artifact_phase.py": (
            "def build(a):\n    return InvokeAgentEffect(agent_name=a, requires_completion_evidence=True)\n"
        ),
    }
    if audit_modules(clean):
        sys.stdout.write(f"SELFTEST FAILED: clean modules produced {audit_modules(clean)}\n")
        return 1
    planted = [
        (
            "ralph/project_policy/planted.py",
            "def pick(controller, agents):\n"
            "    return controller.preferred_agent_index('p', agents).agent\n",
            SELECTION_RULE,
        ),
        (
            "ralph/project_policy/planted.py",
            "def wait(controller, agents):\n"
            "    return controller.earliest_available_wait_ms('p', agents)\n",
            SELECTION_RULE,
        ),
        (
            "ralph/project_policy/planted.py",
            "from ralph.agents.invoke import invoke_agent\n",
            RAW_AGENT_INVOKE_RULE,
        ),
        (
            "ralph/project_policy/planted.py",
            "import ralph.agents.invoke as inv\ndef go(c, p):\n    return inv.invoke_agent(c, p)\n",
            RAW_AGENT_INVOKE_RULE,
        ),
        (
            "ralph/project_policy/planted.py",
            "def build(a, phase):\n"
            "    return InvokeAgentEffect(agent_name=a, requires_completion_evidence=phase == 'x')\n",
            ARTIFACTLESS_ATTEMPT_RULE,
        ),
        (
            "ralph/project_policy/planted.py",
            "def go(**kw):\n    for _ in range(4):\n        run_chain_attempt(**kw)\n",
            CHAIN_RETRY_RULE,
        ),
        (
            "ralph/project_policy/planted.py",
            "def once(**kw):\n    return run_chain_attempt(**kw)\n"
            "def go(**kw):\n    while not once(**kw):\n        pass\n",
            CHAIN_RETRY_RULE,
        ),
    ]
    for rel_path, source, expected in planted:
        rules = [violation.rule for violation in audit_modules({rel_path: source})]
        if expected not in rules:
            sys.stdout.write(f"SELFTEST FAILED: {rel_path} expected {expected}, got {rules}\n")
            return 1
    sys.stdout.write(
        f"AGENT ATTEMPT PATH AUDIT: SELFTEST OK ({len(clean) + len(planted)} cases verified)\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:
        return selftest()
    if selftest() != 0:
        return 1
    repo_root = Path(args[0]) if args else Path(__file__).resolve().parents[2]
    violations = audit_tree(repo_root)
    for violation in violations:
        sys.stdout.write(f"{violation}\n")
    if violations:
        sys.stdout.write(f"AGENT ATTEMPT PATH AUDIT: FAILED ({len(violations)} violation(s))\n")
        return 1
    sys.stdout.write("AGENT ATTEMPT PATH AUDIT: OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
