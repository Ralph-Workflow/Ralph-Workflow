from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.checked_accessors import as_bool, as_int, as_mapping, as_str
from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.pipeline.events.publication_gate_failed_event import PublicationGateFailed
from ralph.pipeline.publication_block import PublicationBlock
from ralph.pipeline.publication_gate_run import PublicationGateRun
from ralph.pipeline.resolution_outcome import WaitingOutcome
from ralph.project_policy.markers import CANONICAL_DIR, FACT_MARKER

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

PUBLICATION_GATE_TIMEOUT_SECONDS: Final[float] = 1800.0
MAX_PUBLICATION_GATE_FAILURES: Final[int] = 5
_GATE_FACT_KEY: Final[str] = "authoritative_verify_command"
_GATE_POLICY_FILE: Final[str] = "verification-policy.md"
_UNRESOLVED_FACT: Final[str] = "PROJECT-FACT-UNRESOLVED"
_RECORD_PATH: Final[str] = ".agent/publication_gate.json"
_OUTPUT_TAIL_CHARS: Final[int] = 6000
_TIMEOUT_EXIT_CODE: Final[int] = 124
_TREE_LOOKUP_TIMEOUT_SECONDS: Final[float] = 30.0
_BACKTICK_COMMAND = re.compile(r"`([^`]+)`")
_FAILED_STEP_MARKERS = (
    re.compile(r"^\s*\[verify\]\s+FAILED:\s*(.+?)\s*$", re.MULTILINE),
    re.compile(r"^FAILED\s+(\S+)", re.MULTILINE),
    re.compile(r"failed while running:\s*(.+?)\s*$", re.MULTILINE),
)
_VOLATILE_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SIGNATURE_FALLBACK_LINES: Final[int] = 5


@dataclass(frozen=True)
class PublicationGateResult:
    command: str
    tree_sha: str
    exit_code: int
    timed_out: bool
    output_tail: str
    consecutive_failures: int = 0
    route_backs: int = 0
    failure_signature: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def block(self) -> PublicationBlock | None:
        if self.passed:
            return None
        return PublicationBlock.GATE_TIMED_OUT if self.timed_out else PublicationBlock.GATE_FAILED

    @property
    def routes_back_to_development(self) -> bool:
        return not self.passed and self.route_backs < MAX_PUBLICATION_GATE_FAILURES

    def feedback(self) -> str:
        verdict = "timed out" if self.timed_out else f"failed (exit {self.exit_code})"
        return (
            f"Publication gate `{self.command}` {verdict} on tree {self.tree_sha}; the run's "
            "commits were not published. Fix the failures below so the gate passes.\n"
            f"{self.output_tail}"
        )


def gate_failure_signature(exit_code: int, *, timed_out: bool, output: str) -> str:
    steps = sorted(
        {
            match.expand(r"\1").strip()
            for marker in _FAILED_STEP_MARKERS
            for match in marker.finditer(output)
        }
    )
    if steps:
        basis = "\n".join(steps)
    else:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        tail = [_VOLATILE_NUMBER.sub("#", line) for line in lines[-_SIGNATURE_FALLBACK_LINES:]]
        basis = "\n".join((f"exit {exit_code} timed out {timed_out}", *tail))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


GateRunner = Callable[[str, Path, float], PublicationGateRun]
TreeLookup = Callable[[Path, str], str]


def publication_gate_record_path(root: Path) -> Path:
    return root / _RECORD_PATH


def declared_publication_gate(root: Path) -> str | None:
    policy = root / CANONICAL_DIR / _GATE_POLICY_FILE
    try:
        lines = policy.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    prefix = f"{FACT_MARKER} {_GATE_FACT_KEY}:"
    for line in lines:
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if not value or value.startswith(_UNRESOLVED_FACT):
            return None
        quoted = _BACKTICK_COMMAND.search(value)
        return as_str(quoted.group(1), field="gate command").strip() if quoted else value
    return None


def _run_gate_command(command: str, cwd: Path, timeout_seconds: float) -> PublicationGateRun:
    try:
        result = run_process(
            "sh",
            ("-c", command),
            options=ProcessRunOptions(cwd=cwd, timeout=timeout_seconds, label="publication-gate"),
        )
    except ProcessExecutionError as exc:
        return PublicationGateRun(exit_code=_TIMEOUT_EXIT_CODE, output=str(exc), timed_out=True)
    return PublicationGateRun(
        exit_code=result.returncode,
        output=f"{result.stdout}{result.stderr}",
        timed_out=result.returncode == _TIMEOUT_EXIT_CODE,
    )


def _tree_of(root: Path, commit: str) -> str:
    result = run_git(
        ("rev-parse", "--verify", f"{commit}^{{tree}}"),
        cwd=root,
        label="publication-gate-tree",
        options=GitRunOptions(timeout=_TREE_LOOKUP_TIMEOUT_SECONDS),
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _load_record(root: Path) -> PublicationGateResult | None:
    try:
        raw: object = json.loads(publication_gate_record_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        fields = as_mapping(raw, field="publication gate record")
        output_tail = fields["output_tail"]
        if not isinstance(output_tail, str):
            return None
        return PublicationGateResult(
            command=as_str(fields["command"], field="command"),
            tree_sha=as_str(fields["tree_sha"], field="tree_sha"),
            exit_code=as_int(fields["exit_code"], field="exit_code"),
            timed_out=as_bool(fields["timed_out"], field="timed_out"),
            output_tail=output_tail,
            consecutive_failures=as_int(
                fields.get("consecutive_failures", 0), field="consecutive_failures"
            ),
            route_backs=as_int(fields.get("route_backs", 0), field="route_backs"),
            failure_signature=as_str(
                fields.get("failure_signature", ""), field="failure_signature"
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _save_record(root: Path, result: PublicationGateResult) -> None:
    destination = publication_gate_record_path(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        destination,
        json.dumps(
            _record_payload(result),
            sort_keys=True,
        ),
        tmp_path=destination.with_suffix(".tmp"),
    )


def _record_payload(result: PublicationGateResult) -> dict[str, object]:
    return {
        "command": result.command,
        "tree_sha": result.tree_sha,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "output_tail": result.output_tail,
        "consecutive_failures": result.consecutive_failures,
        "route_backs": result.route_backs,
        "failure_signature": result.failure_signature,
    }


def check_publication_gate(
    root: Path,
    feature_sha: str,
    *,
    timeout_seconds: float = PUBLICATION_GATE_TIMEOUT_SECONDS,
    runner: GateRunner | None = None,
    tree_of: TreeLookup | None = None,
) -> PublicationGateResult | None:
    command = declared_publication_gate(root)
    if command is None:
        return None
    tree_sha = (tree_of or _tree_of)(root, feature_sha)
    previous = _load_record(root)
    if (
        previous is not None
        and tree_sha
        and previous.tree_sha == tree_sha
        and previous.command == command
    ):
        return previous
    logger.info("Running the publication gate `{}` on tree {}", command, tree_sha)
    run = (runner or _run_gate_command)(command, root, timeout_seconds)
    result = PublicationGateResult(
        command=command,
        tree_sha=tree_sha,
        exit_code=run.exit_code,
        timed_out=run.timed_out,
        output_tail=run.output[-_OUTPUT_TAIL_CHARS:],
    )
    failures = 0 if result.passed else (previous.consecutive_failures if previous else 0) + 1
    signature = (
        ""
        if result.passed
        else gate_failure_signature(run.exit_code, timed_out=run.timed_out, output=run.output)
    )
    same_failure = previous is not None and previous.failure_signature == signature
    route_backs = previous.route_backs if same_failure and previous is not None else 0
    result = replace(
        result,
        consecutive_failures=failures,
        route_backs=0 if result.passed else route_backs,
        failure_signature=signature,
    )
    if not result.passed:
        logger.warning(
            "Publication gate `{}` blocked tree {} (exit {}, timed out: {})",
            command,
            tree_sha,
            result.exit_code,
            result.timed_out,
        )
    _save_record(root, result)
    return result


def last_publication_gate_result(root: Path) -> PublicationGateResult | None:
    return _load_record(root)


def blocked_publication(
    root: Path, feature_sha: str = "HEAD", *, tree_of: TreeLookup | None = None
) -> PublicationGateResult | None:
    record = _load_record(root)
    if record is None or record.passed or declared_publication_gate(root) != record.command:
        return None
    tree_sha = (tree_of or _tree_of)(root, feature_sha)
    return record if tree_sha and tree_sha == record.tree_sha else None


def publication_gate_refusal(
    root: Path, feature_sha: str
) -> tuple[WaitingOutcome, PublicationBlock] | None:
    result = check_publication_gate(root, feature_sha)
    if result is None or result.block is None:
        return None
    verdict = "timed out" if result.timed_out else f"failed (exit {result.exit_code})"
    return (
        WaitingOutcome(
            external_condition=(
                f"publication gate `{result.command}` {verdict} on tree {result.tree_sha}"
            ),
            resumes_when="the publication gate passes on the run's next tree",
        ),
        result.block,
    )


def publication_gate_feedback(
    root: Path, policy: PipelinePolicy, commit_phase: str
) -> PublicationGateFailed | None:
    result = _load_record(root)
    loop_phase = publication_gate_loop_phase(policy, commit_phase)
    if result is None or not result.routes_back_to_development or loop_phase is None:
        return None
    _save_record(root, replace(result, route_backs=result.route_backs + 1))
    _write_development_feedback(root, policy, loop_phase, result)
    return PublicationGateFailed(phase=commit_phase, detail=result.feedback())


def _write_development_feedback(
    root: Path, policy: PipelinePolicy, loop_phase: str, result: PublicationGateResult
) -> bool:
    loop_target = policy.phases[loop_phase].transitions.on_loopback
    if not loop_target:
        return False
    from ralph.phases.required_artifacts import retry_hint_path

    hint = root / retry_hint_path(loop_target, pipeline_policy=policy)
    hint.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write_text_if_changed(
        DEFAULT_FILE_BACKEND, hint, result.feedback(), tmp_path=hint.with_suffix(".tmp")
    )


def leave_publication_gate_feedback(root: Path, policy: PipelinePolicy) -> bool:
    result = _load_record(root)
    if result is None or not result.routes_back_to_development:
        return False
    loop_phase = next(
        (
            loop
            for name, definition in policy.phases.items()
            if definition.role == "commit"
            and (loop := publication_gate_loop_phase(policy, name)) is not None
        ),
        None,
    )
    if loop_phase is None or not _write_development_feedback(root, policy, loop_phase, result):
        return False
    _save_record(root, replace(result, route_backs=result.route_backs + 1))
    logger.warning(
        "Publication gate `{}` refused tree {} outside a commit phase; its failure is left "
        "for the next development pass",
        result.command,
        result.tree_sha,
    )
    return True


def _upstream_phases(policy: PipelinePolicy, phase: str) -> list[str]:
    return [
        name
        for name, definition in policy.phases.items()
        if definition.transitions.on_success == phase
    ]


def _is_loop_analysis(policy: PipelinePolicy, phase: str) -> bool:
    definition = policy.phases.get(phase)
    return (
        definition is not None
        and definition.role == "analysis"
        and definition.loop_policy is not None
        and bool(definition.transitions.on_loopback)
    )


def publication_gate_loop_phase(policy: PipelinePolicy, commit_phase: str) -> str | None:
    definition = policy.phases.get(commit_phase)
    if definition is None:
        return None
    downstream = definition.transitions.on_success
    if downstream is not None and _is_loop_analysis(policy, downstream):
        return downstream
    frontier = [commit_phase]
    visited = {commit_phase}
    while frontier:
        upstream = [
            name
            for phase in frontier
            for name in _upstream_phases(policy, phase)
            if name not in visited
        ]
        for name in upstream:
            if _is_loop_analysis(policy, name):
                return name
        visited.update(upstream)
        frontier = upstream
    return None


__all__ = [
    "MAX_PUBLICATION_GATE_FAILURES",
    "PUBLICATION_GATE_TIMEOUT_SECONDS",
    "GateRunner",
    "PublicationBlock",
    "PublicationGateFailed",
    "PublicationGateResult",
    "PublicationGateRun",
    "TreeLookup",
    "blocked_publication",
    "check_publication_gate",
    "declared_publication_gate",
    "gate_failure_signature",
    "last_publication_gate_result",
    "leave_publication_gate_feedback",
    "publication_gate_feedback",
    "publication_gate_loop_phase",
    "publication_gate_record_path",
    "publication_gate_refusal",
]
