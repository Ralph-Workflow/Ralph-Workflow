from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _PACKAGE_ROOT.parent

LEDGER_MODULE = "recovery/agent_failure_ledger.py"
TRACKER_MODULE = "recovery/agent_unavailability_tracker.py"
WAIT_KIND_MODULE = "recovery/wait_kind.py"
SELECTION_MODULE = "recovery/agent_selection.py"

STATUS_BASE = "_AgentStatus"
ALLOWED_STATUS_CLASSES: frozenset[str] = frozenset({"_Eligible", "_Cooldown"})
EXPIRY_FIELD = "until_ms"
ALLOWED_WAIT_KINDS: frozenset[str] = frozenset({"AVAILABLE_NOW", "COOLING_DOWN", "NO_CANDIDATES"})
ALLOWED_ELIGIBILITY_ALIAS_NAMES: frozenset[str] = frozenset({"Eligible", "Cooldown", "CoolingDown"})
ALLOWED_MARK_UNAVAILABLE_MODULES: frozenset[str] = frozenset(
    {"recovery/controller.py", LEDGER_MODULE, TRACKER_MODULE}
)

FORBIDDEN_IDENTIFIER = re.compile(
    r"(?i)(^|_)(spent|demot\w*|bench(ed|es)?|benched)(_|$)"
    r"|spent_agents|retry_exhaust|all_exhausted|requires_intervention|never_eligible"
    r"|skipped_candidates|is_unavailable_state"
)
FORBIDDEN_IDENTIFIER_SCOPES: tuple[str, ...] = ("recovery/", "pipeline/")
FORBIDDEN_IDENTIFIER_EXEMPT: frozenset[str] = frozenset(
    {
        "rounds_spent",
        "stops_spent",
        "record_requires_intervention",
    }
)

FORBIDDEN_IDENTIFIER_EXEMPT_PARTS: tuple[str, ...] = ("budget",)

SOLE_COOLDOWN_DOC = "docs/agents/cooldown-invariant.md"
TERMINOLOGY_HEADING = "## Terminology: every name means the cooldown system"
TWO_STATES_STATEMENT = "exactly two states"
TERMINOLOGY_ALIASES: tuple[str, ...] = (
    "bench",
    "benched",
    "benching",
    "demote",
    "demoted",
    "demotion",
    "spent",
    "exhausted",
    "unavailable",
    "excluded",
    "skipped",
    "dead-agent bench",
)
COOLDOWN_DOC_POINTER = "COOLDOWN_SYSTEM_DOC"
COOLDOWN_DOC_POINTER_MODULES: tuple[str, ...] = (TRACKER_MODULE, LEDGER_MODULE)

AGENT_VOCABULARY = re.compile(r"(?i)bench|spent|demot|exhaust")
AGENT_VOCABULARY_SCOPES: tuple[str, ...] = (
    "recovery/",
    "pipeline/conflict_resolution/",
    "pipeline/_stop_resolution_entry.py",
    "pipeline/_integration_status.py",
    "pipeline/_integration_continuation.py",
    "pipeline/_auto_integrate_rebase_resolve.py",
    "pipeline/resolution_outcome_producer.py",
    "pipeline/resolution_outcome_cooling_down.py",
    "pipeline/auto_integrate_agent.py",
    "pipeline/agent_retry_decision.py",
    "pipeline/attempt_runner.py",
    "pipeline/cooldown_wait_context.py",
    "pipeline/run_time_report.py",
    "pipeline/reducer.py",
    "agents/agent_chain.py",
)
LEGACY_MIGRATION_MODULE = "recovery/legacy_guard_migration.py"
AGENT_VOCABULARY_ALLOWED_TOKENS: dict[tuple[str, str], str] = {
    (LEGACY_MIGRATION_MODULE, "false_bench_feedback"): (
        "legacy checkpoint key, read only to migrate old checkpoints onto false_cooldown_feedback"
    ),
    (LEGACY_MIGRATION_MODULE, "is_exhausted"): (
        "legacy progress-guard checkpoint field, read only to migrate old checkpoints"
    ),
}
AGENT_VOCABULARY_ALLOWED_PHRASES: dict[str, str] = {
    r"(?i)(?:quota|credits?|resource|context|token|rate.?limit)[ _-]*exhaust\w*": (
        "a provider resource (quota, credits, context window) ran out; the agent's cooldown "
        "reason names the resource, not an availability state"
    ),
    r"(?i)exhaust\w*[ _-]*(?:quota|credits?|resource)": "provider resource wording, as above",
    r"(?i)\w*(?:Quota|Context)Exhausted\w*": "typed provider error classes",
    r"(?i)\w*budget\w*": "a CPU, probe, conflict or analysis budget, not an agent",
    r"(?i)quota or rate limit is exhausted": "the typed provider quota error message",
    r"(?i)(?:rounds|stops)_spent": "per-stop round and stop counters",
    r"(?i)\w*exhausted_analysis\w*|ExhaustedAnalysis\w*": "the analysis-iteration budget bypass",
    r"(?i)\w*resolution_exhaust\w*": (
        "the persisted RebaseState conflict-resolution ladder fields (checkpoint schema)"
    ),
    r"(?i)commit-recovery counter exhausted": "the commit-recovery counter cap",
    r"(?i)(?:tool-registry-reset|recovery-attempt|restart) exhausted": "MCP recovery budgets",
}

TEST_TREE = "tests"
_TEST_FUNCTION = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test_\w+)", re.MULTILINE)
TEST_NAME_ALLOWED_FILES: dict[str, str] = {
    "tests/test_explore_": "the explore `bench` benchmark harness, not an agent",
    "tests/mcp/explore/": "the explore `bench` benchmark harness, not an agent",
    "tests/unit/display/": "display colour demotion, not an agent",
    "tests/display/": "display colour demotion, not an agent",
    "tests/testing/test_repo_structure_boundary_rules.py": (
        "planted tests of rule B2, which bans the removed `_spent_agents`, `Spent` and "
        "`current_allowance_spent` names; the test names must name the banned identifiers"
    ),
}
TEST_NAME_ALLOWED_PHRASES: dict[str, str] = {
    r"legacy_spent": "a legacy-migration test that must name the legacy checkpoint field",
    r"exhaustive": "exhaustive matching, not exhaustion",
    r"iterator_exhaustion|on_iterator_exhaustion|exhaustion_flushes": "a parser input iterator",
    r"capacity_exhaustion|candidate_limit_exhaustion|registry_exhausts": (
        "a file-watch or MCP process capacity limit"
    ),
    r"spent_cycle|cycles_exhausted": "the development cycle timebox",
    r"(?:counter|loop|rejection_limit)_exhaustion|post_commit_exhausted|exhausted_gate"
    r"|when_exhausted": "a policy gate counter or loop budget",
    r"exhausted_mid_run_verdict|recorded_exhaustion|exhausted_record|non_exhausted_state"
    r"|exhaustion_does_not_outlive|exhaustion_is_terminal|exhaustion_survives|exhaustion_calls"
    r"|exhausted_conflict_ladder|conflict_cap_exhaustion|cold_exhaust\w*|exhaustion_across_restarts"
    r"|exhaustion_restart|exhausted_retry|backoff_survives_restart_and_exhaustion": (
        "the persisted integration conflict ladder and its retry budget"
    ),
    r"exhausted_integration_attempts|per_seam_exhaustion|exhausted_diagnostic_attempts": (
        "integration and diagnostic attempt budgets"
    ),
    r"exhausted_commit_failure|backoff_exhausted|retry_cap_exhaustion|exhausts_retries": (
        "a git, commit or transport retry cap"
    ),
    r"exhausted_sentinel|exhausted_bypass|exhausted_\w+_analysis|commit_exhausted": (
        "the analysis-iteration and commit loop budgets"
    ),
    r"cap_exhaustion|diagnostic_exhaustion|exhaustion_does_not_invoke|cap_is_spent": (
        "a diagnostic, integration-retry or verify hang cap"
    ),
    r"demotes_to_degraded|demotes_run|classification_not_demoted|vendor_path_demoted": (
        "evidence grading, liveness classification or git-status ordering"
    ),
}

DOC_AGENT_PHRASE = re.compile(
    r"(?i)\b(?:benched|spent|demoted|exhausted) (?:resolver |fallback )?agents?\b"
    r"|\bagents? (?:is|are|was|were|gets?|got|becomes?|becoming) (?:benched|spent|demoted)\b"
    r"|\b(?:bench|benches|benching|demote|demotes|demoting) (?:an |the |that |this |each |every )?"
    r"(?:resolver |fallback )?agents?\b"
    r"|\b(?:agent|resolver) demotion\b|\bdemotion of (?:an |the )?agents?\b"
    r"|\bfalse[- ]bench(?:es)?\b|\bdead-agent bench\b|spent/unavailable"
    r"|\b(?:agent|resolver) chain (?:is |was )?(?:spent|exhausted)\b"
    r"|\bexhausts? its retr(?:y|ies)\b"
)
DOC_COOLDOWN_HEADING = re.compile(
    r"(?i)cooldown|cool-down|\bdemot|\bbench(?:ed|es|ing)?\b|agent availability"
    r"|\bbackoff\b|failure limits|unproductive"
)
_MAX_HEADING_LEVEL = 6
_DOC_PHRASE_STEMS: tuple[str, ...] = ("bench", "spent", "demot", "exhaust")
_TERMINOLOGY_LEVEL = len(TERMINOLOGY_HEADING.split(" ", 1)[0])

GUARD_TESTS: dict[str, tuple[str, ...]] = {
    "tests/recovery/test_cooldown_only_invariant.py": (
        "test_every_failure_sequence_leaves_the_agent_cooling_down_and_then_selectable",
        "test_a_sole_agent_past_its_unproductive_limit_waits_for_its_cooldown_instead_of_failing",
    ),
    "tests/recovery/test_unproductive_limit_cooldown_regression.py": (
        "test_an_agent_past_the_total_unproductive_limit_is_retried_after_its_own_cooldown",
        "test_the_unproductive_limit_cooldown_names_its_reason_and_is_shorter_than_out_of_credits",
    ),
    "tests/test_audit_cooldown_only.py": (
        "test_the_repository_passes_the_cooldown_only_audit",
        "test_a_status_class_without_an_expiry_fails_the_audit",
    ),
}
_DISABLING_MARKERS: frozenset[str] = frozenset({"skip", "skipif", "xfail"})


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.detail}"


def _parse(source: str, rel_path: str) -> ast.Module | Violation:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        return Violation(rel_path, exc.lineno or 0, f"cannot parse: {exc.msg}")


def _base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _annotated_fields(node: ast.ClassDef) -> dict[str, str]:
    return {
        stmt.target.id: ast.unparse(stmt.annotation)
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    }


def status_class_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not ({STATUS_BASE, "AgentStatus"} & _base_names(node)):
            continue
        if node.name not in ALLOWED_STATUS_CLASSES:
            violations.append(
                Violation(
                    rel_path,
                    node.lineno,
                    f"agent status class {node.name} is a third availability state; an agent "
                    "is either eligible or on an expiring Cooldown",
                )
            )
            continue
        fields = _annotated_fields(node)
        if node.name == "_Cooldown" and fields.get(EXPIRY_FIELD) != "int":
            violations.append(
                Violation(
                    rel_path, node.lineno, f"_Cooldown must carry a required `{EXPIRY_FIELD}: int`"
                )
            )
        if node.name == "_Eligible" and fields:
            violations.append(Violation(rel_path, node.lineno, "_Eligible must carry no state"))
    return violations


def eligibility_alias_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.TypeAlias) and node.name.id == "AgentEligibility":
            names = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
            extra = names - ALLOWED_ELIGIBILITY_ALIAS_NAMES
            if extra:
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        f"AgentEligibility admits non-cooldown states: {sorted(extra)}",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "AgentEligibility type alias is missing")]


def wait_kind_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "WaitKind":
            members = {
                target.id
                for stmt in node.body
                if isinstance(stmt, ast.Assign)
                for target in stmt.targets
                if isinstance(target, ast.Name)
            }
            extra = members - ALLOWED_WAIT_KINDS
            if extra:
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        f"WaitKind admits a wait with no cooldown expiry: {sorted(extra)}",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "WaitKind enum is missing")]


def tracker_entry_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "UnavailabilityEntry":
            if _annotated_fields(node).get("unavailable_until_ms") != "int":
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        "UnavailabilityEntry.unavailable_until_ms must be a required int expiry",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "UnavailabilityEntry is missing")]


def selection_row_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "agent_availability":
            params = [arg.arg for arg in node.args.args]
            expected = ["agent", "available", "cooldown_ms_remaining", "cooldown_reason"]
            if params != expected:
                return [
                    Violation(
                        rel_path,
                        node.lineno,
                        f"selection rows may only carry availability and cooldown, got {params}",
                    )
                ]
            return []
    return [Violation(rel_path, 1, "agent_availability is missing")]


def _identifiers(tree: ast.Module) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.lineno, node.name))
        elif isinstance(node, ast.Name):
            found.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((node.lineno, node.attr))
        elif isinstance(node, ast.arg):
            found.append((node.lineno, node.arg))
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.append((node.value.lineno, node.arg))
    return found


def forbidden_identifier_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    if not rel_path.startswith(FORBIDDEN_IDENTIFIER_SCOPES):
        return []
    return [
        Violation(
            rel_path,
            line,
            f"identifier {name!r} names an agent exclusion state outside the cooldown system",
        )
        for line, name in _identifiers(tree)
        if name not in FORBIDDEN_IDENTIFIER_EXEMPT
        and not any(part in name.lower() for part in FORBIDDEN_IDENTIFIER_EXEMPT_PARTS)
        and FORBIDDEN_IDENTIFIER.search(name)
    ]


def _string_constants(tree: ast.Module) -> list[tuple[int, str]]:
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _names_old_agent_vocabulary(text: str, rel_path: str) -> bool:
    if (rel_path, text) in AGENT_VOCABULARY_ALLOWED_TOKENS:
        return False
    remaining = text
    for phrase in AGENT_VOCABULARY_ALLOWED_PHRASES:
        remaining = re.sub(phrase, " ", remaining)
    return AGENT_VOCABULARY.search(remaining) is not None


def agent_vocabulary_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    if not rel_path.startswith(AGENT_VOCABULARY_SCOPES):
        return []
    violations = [
        Violation(
            rel_path,
            line,
            f"identifier {name!r} uses bench/spent/demot/exhaust vocabulary for agent "
            "availability; every agent exclusion is the cooldown system",
        )
        for line, name in _identifiers(tree)
        if _names_old_agent_vocabulary(name, rel_path)
    ]
    violations.extend(
        Violation(
            rel_path,
            line,
            f"string {text[:60]!r} uses bench/spent/demot/exhaust vocabulary for agent "
            "availability; describe it as a cooldown",
        )
        for line, text in _string_constants(tree)
        if _names_old_agent_vocabulary(text, rel_path)
    )
    return violations


def cooldown_doc_pointer_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
        if any(isinstance(t, ast.Name) and t.id == COOLDOWN_DOC_POINTER for t in targets):
            if isinstance(value, ast.Constant) and value.value == SOLE_COOLDOWN_DOC:
                return []
            break
    return [
        Violation(
            rel_path,
            1,
            f"{COOLDOWN_DOC_POINTER} must name the sole cooldown-system doc {SOLE_COOLDOWN_DOC}",
        )
    ]


def mark_unavailable_violations(tree: ast.Module, rel_path: str) -> list[Violation]:
    if rel_path in ALLOWED_MARK_UNAVAILABLE_MODULES:
        return []
    return [
        Violation(
            rel_path,
            node.lineno,
            "only the recovery controller and the failure ledger may write the cooldown tracker",
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "mark_unavailable"
    ]


def _module_checks(tree: ast.Module, rel_path: str, source: str) -> list[Violation]:
    violations: list[Violation] = []
    if "AgentStatus" in source:
        violations.extend(status_class_violations(tree, rel_path))
    if _SCOPED_TRIGGER.search(source) is not None:
        violations.extend(forbidden_identifier_violations(tree, rel_path))
        violations.extend(agent_vocabulary_violations(tree, rel_path))
    if rel_path in COOLDOWN_DOC_POINTER_MODULES:
        violations.extend(cooldown_doc_pointer_violations(tree, rel_path))
    if "mark_unavailable" in source:
        violations.extend(mark_unavailable_violations(tree, rel_path))
    if rel_path == LEDGER_MODULE:
        violations.extend(eligibility_alias_violations(tree, rel_path))
    if rel_path == WAIT_KIND_MODULE:
        violations.extend(wait_kind_violations(tree, rel_path))
    if rel_path == TRACKER_MODULE:
        violations.extend(tracker_entry_violations(tree, rel_path))
    if rel_path == SELECTION_MODULE:
        violations.extend(selection_row_violations(tree, rel_path))
    return violations


_SPECIAL_MODULES: frozenset[str] = frozenset(
    {LEDGER_MODULE, TRACKER_MODULE, WAIT_KIND_MODULE, SELECTION_MODULE}
)
_SCOPED_TRIGGER = re.compile(
    r"(?i)spent|demot|bench|exhaust|intervention|never_eligible|skipped_candidates"
    r"|is_unavailable_state"
)


def _needs_parse(source: str, rel_path: str) -> bool:
    if rel_path in _SPECIAL_MODULES or "AgentStatus" in source or "mark_unavailable" in source:
        return True
    return rel_path.startswith(FORBIDDEN_IDENTIFIER_SCOPES + AGENT_VOCABULARY_SCOPES) and (
        _SCOPED_TRIGGER.search(source) is not None
    )


def source_violations(source: str, rel_path: str) -> list[Violation]:
    if not _needs_parse(source, rel_path):
        return []
    parsed = _parse(source, rel_path)
    if isinstance(parsed, Violation):
        return [parsed]
    return _module_checks(parsed, rel_path, source)


def _package_source_paths(package_root: Path) -> tuple[str, ...]:
    rel_paths = tuple(
        path.relative_to(package_root).as_posix() for path in sorted(package_root.rglob("*.py"))
    )
    return tuple(rel_path for rel_path in rel_paths if not rel_path.startswith("testing/"))


def recovery_source_paths(package_root: Path = _PACKAGE_ROOT) -> tuple[str, ...]:
    recovery_root = package_root / "recovery"
    if not recovery_root.is_dir():
        return ()
    return tuple(
        path.relative_to(package_root).as_posix() for path in sorted(recovery_root.rglob("*.py"))
    )


def source_violations_for_paths(
    paths: Sequence[str], package_root: Path = _PACKAGE_ROOT
) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path in paths:
        violations.extend(
            source_violations((package_root / rel_path).read_text(encoding="utf-8"), rel_path)
        )
    return violations


def _heading(line: str) -> tuple[int, str] | None:
    level = len(line) - len(line.lstrip("#"))
    if not 1 <= level <= _MAX_HEADING_LEVEL or line[level : level + 1] not in {" ", "\t"}:
        return None
    return level, line[level:].strip()


def _markdown_sections(text: str) -> list[tuple[int, int, str, list[str]]]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = [
        (index, heading[0], heading[1])
        for index, line in enumerate(lines)
        if (heading := _heading(line)) is not None
    ]
    sections: list[tuple[int, int, str, list[str]]] = []
    for position, (index, level, title) in enumerate(headings):
        end = len(lines)
        for later_index, later_level, _title in headings[position + 1 :]:
            if later_level <= level:
                end = later_index
                break
        sections.append((index + 1, level, title, lines[index:end]))
    return sections


def _terminology_section(text: str) -> tuple[int, int] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == TERMINOLOGY_HEADING:
            end = next(
                (
                    later
                    for later in range(index + 1, len(lines))
                    if (heading := _heading(lines[later])) is not None
                    and heading[0] <= _TERMINOLOGY_LEVEL
                ),
                len(lines),
            )
            return index, end
    return None


def sole_doc_violations(text: str | None, rel_path: str = SOLE_COOLDOWN_DOC) -> list[Violation]:
    if text is None:
        return [Violation(rel_path, 0, "the sole cooldown-system document is missing")]
    span = _terminology_section(text)
    if span is None:
        return [
            Violation(
                rel_path, 0, f"the sole cooldown-system document lacks {TERMINOLOGY_HEADING!r}"
            )
        ]
    start, end = span
    section = "\n".join(text.splitlines()[start:end]).lower()
    violations = [
        Violation(
            rel_path,
            start + 1,
            f"the Terminology section does not map the alias {alias!r} to the cooldown system",
        )
        for alias in TERMINOLOGY_ALIASES
        if re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", section) is None
    ]
    if TWO_STATES_STATEMENT not in section:
        violations.append(
            Violation(
                rel_path,
                start + 1,
                f"the Terminology section must state that an agent has {TWO_STATES_STATEMENT}",
            )
        )
    return violations


def doc_phrase_violations(text: str, rel_path: str) -> list[Violation]:
    exempt: tuple[int, int] | None = (
        _terminology_section(text) if rel_path.endswith(SOLE_COOLDOWN_DOC) else None
    )
    return [
        Violation(
            rel_path,
            number,
            f"{match.group(0)!r} describes agent availability outside the cooldown system; "
            f"an excluded agent is on cooldown (see {SOLE_COOLDOWN_DOC})",
        )
        for number, line in enumerate(text.splitlines(), start=1)
        if any(stem in line.lower() for stem in _DOC_PHRASE_STEMS)
        and (exempt is None or not (exempt[0] < number <= exempt[1]))
        for match in DOC_AGENT_PHRASE.finditer(line)
    ]


def doc_heading_violations(text: str, rel_path: str) -> list[Violation]:
    if rel_path.endswith(SOLE_COOLDOWN_DOC):
        return []
    link_target = SOLE_COOLDOWN_DOC.rsplit("/", 1)[-1]
    return [
        Violation(
            rel_path,
            line,
            f"section {title!r} describes agent cooldown or availability without linking "
            f"to the sole cooldown-system document {SOLE_COOLDOWN_DOC}",
        )
        for line, _level, title, body in _markdown_sections(text)
        if DOC_COOLDOWN_HEADING.search(title) is not None
        and not any(link_target in body_line for body_line in body)
    ]


def agents_md_violations(text: str | None, rel_path: str) -> list[Violation]:
    if text is not None and SOLE_COOLDOWN_DOC in text:
        return []
    return [
        Violation(
            rel_path,
            0,
            f"AGENTS.md must name the sole cooldown-system document {SOLE_COOLDOWN_DOC}",
        )
    ]


def _documentation_files(project_root: Path) -> list[tuple[Path, str]]:
    roots = [(project_root, "")]
    checkout_root = project_root.parent
    if (checkout_root / "AGENTS.md").is_file() and (checkout_root / "docs").is_dir():
        roots.append((checkout_root, "../"))
    found: list[tuple[Path, str]] = []
    for root, prefix in roots:
        docs_dir = root / "docs"
        if docs_dir.is_dir():
            found.extend(
                (path, prefix + path.relative_to(root).as_posix())
                for path in sorted(docs_dir.rglob("*.md"))
                if "_build" not in path.parts
            )
        found.extend(
            (root / name, prefix + name)
            for name in ("AGENTS.md", "CONTRIBUTING.md")
            if (root / name).is_file()
        )
    return found


def documentation_violations(project_root: Path = _PROJECT_ROOT) -> list[Violation]:
    sole_doc = project_root / SOLE_COOLDOWN_DOC
    violations = sole_doc_violations(
        sole_doc.read_text(encoding="utf-8") if sole_doc.is_file() else None
    )
    checkout_agents = project_root.parent / "AGENTS.md"
    agents_md = checkout_agents if checkout_agents.is_file() else project_root / "AGENTS.md"
    violations.extend(
        agents_md_violations(
            agents_md.read_text(encoding="utf-8") if agents_md.is_file() else None,
            "AGENTS.md",
        )
    )
    for path, rel_path in _documentation_files(project_root):
        text = path.read_text(encoding="utf-8")
        violations.extend(doc_phrase_violations(text, rel_path))
        if "/docs/" in f"/{rel_path}":
            violations.extend(doc_heading_violations(text, rel_path))
    return violations


def _test_name_allowed(name: str, rel_path: str) -> bool:
    lowered = name.lower()
    if not any(stem in lowered for stem in _DOC_PHRASE_STEMS):
        return True
    if rel_path.startswith(tuple(TEST_NAME_ALLOWED_FILES)):
        return True
    remaining = name
    for phrase in (*AGENT_VOCABULARY_ALLOWED_PHRASES, *TEST_NAME_ALLOWED_PHRASES):
        remaining = re.sub(phrase, " ", remaining)
    return AGENT_VOCABULARY.search(remaining) is None


def module_test_name_violations(text: str, rel_path: str) -> list[Violation]:
    stem = rel_path.rsplit("/", 1)[-1].removesuffix(".py")
    violations: list[Violation] = []
    if not _test_name_allowed(stem, rel_path):
        violations.append(
            Violation(
                rel_path,
                0,
                f"test module {stem!r} uses bench/spent/demot/exhaust vocabulary for agent "
                "availability; name it after the cooldown",
            )
        )
    if not any(stem in text for stem in _DOC_PHRASE_STEMS):
        return violations
    violations.extend(
        Violation(
            rel_path,
            text.count("\n", 0, match.start()) + 1,
            f"test {match.group(1)!r} uses bench/spent/demot/exhaust vocabulary for agent "
            "availability; name it after the cooldown",
        )
        for match in _TEST_FUNCTION.finditer(text)
        if not _test_name_allowed(match.group(1), rel_path)
    )
    return violations


def test_name_scan_paths(project_root: Path = _PROJECT_ROOT) -> tuple[Path, ...]:
    tests_dir = project_root / TEST_TREE
    if not tests_dir.is_dir():
        return ()
    return tuple(sorted(tests_dir.rglob("test_*.py")))


def tree_test_name_violations(
    project_root: Path = _PROJECT_ROOT, paths: Sequence[Path] | None = None
) -> list[Violation]:
    scan_paths = test_name_scan_paths(project_root) if paths is None else tuple(paths)
    violations: list[Violation] = []
    for path in scan_paths:
        rel_path = path.relative_to(project_root).as_posix()
        violations.extend(module_test_name_violations(path.read_text(encoding="utf-8"), rel_path))
    return violations


def _decorator_disables(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(target, ast.Attribute) and target.attr in _DISABLING_MARKERS


def guard_test_violations(
    source: str | None, rel_path: str, names: tuple[str, ...]
) -> list[Violation]:
    if source is None:
        return [Violation(rel_path, 0, "cooldown-only guard test module was deleted")]
    parsed = _parse(source, rel_path)
    if isinstance(parsed, Violation):
        return [parsed]
    functions = {node.name: node for node in parsed.body if isinstance(node, ast.FunctionDef)}
    module_disabled = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
        and any(
            isinstance(sub, ast.Attribute) and sub.attr in _DISABLING_MARKERS
            for sub in ast.walk(node.value)
        )
        for node in parsed.body
    )
    violations: list[Violation] = []
    if module_disabled:
        violations.append(Violation(rel_path, 0, "cooldown-only guard module is skip-marked"))
    for name in names:
        function = functions.get(name)
        if function is None:
            violations.append(
                Violation(rel_path, 0, f"cooldown-only guard test {name} was removed")
            )
        elif any(_decorator_disables(decorator) for decorator in function.decorator_list):
            violations.append(
                Violation(rel_path, function.lineno, f"cooldown-only guard test {name} is disabled")
            )
    return violations


def _guard_test_module_violations(project_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path, names in GUARD_TESTS.items():
        guard_path = project_root / rel_path
        source = guard_path.read_text(encoding="utf-8") if guard_path.is_file() else None
        violations.extend(guard_test_violations(source, rel_path, names))
    return violations


def guard_test_module_violations(package_root: Path = _PACKAGE_ROOT) -> list[Violation]:
    return _guard_test_module_violations(package_root.parent)


def collect_violations(
    package_root: Path = _PACKAGE_ROOT,
    project_root: Path = _PROJECT_ROOT,
) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path in _package_source_paths(package_root):
        violations.extend(
            source_violations((package_root / rel_path).read_text(encoding="utf-8"), rel_path)
        )
    violations.extend(_guard_test_module_violations(project_root))
    violations.extend(documentation_violations(project_root))
    violations.extend(tree_test_name_violations(project_root))
    return violations


def main(argv: list[str] | None = None) -> int:
    del argv
    violations = collect_violations()
    if violations:
        sys.stdout.write(f"COOLDOWN-ONLY AUDIT FAILED: {len(violations)} violation(s)\n")
        sys.stdout.write("=" * 72 + "\n")
        for violation in violations:
            sys.stdout.write(f"  {violation}\n")
        sys.stdout.write(
            "An agent is either on an expiring cooldown or available; no other exclusion "
            "state may exist.\n"
        )
        return 1
    sys.stdout.write("Cooldown-only audit OK: every agent exclusion is an expiring cooldown.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
