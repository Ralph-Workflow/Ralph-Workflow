from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.checked_accessors import as_bool, as_int, as_mapping, as_str
from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.failure_digest import failure_line_digest
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed
from ralph.pipeline.events.publication_gate_failed_event import PublicationGateFailed
from ralph.pipeline.gate_declaration import GateDeclaration
from ralph.pipeline.publication_block import PublicationBlock
from ralph.pipeline.publication_gate_run import PublicationGateRun
from ralph.pipeline.publication_gate_slots import publication_gate_slot
from ralph.process.session_orphan_reaper import reaped_process_scope
from ralph.project_policy.markers import CANONICAL_DIR, FACT_MARKER

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

PUBLICATION_GATE_TIMEOUT_SECONDS: Final[float] = 1800.0
MIN_PUBLICATION_GATE_TIMEOUT_SECONDS: Final[float] = 300.0
PASS_TIME_TIMEOUT_MULTIPLIER: Final[float] = 10.0
MAX_PUBLICATION_GATE_FAILURES: Final[int] = 5
MAX_INCONCLUSIVE_GATE_RUNS: Final[int] = 3
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
_CANCELLED_MARKER = re.compile(r"^\s*\[verify\]\s+CANCELLED\b", re.MULTILINE)
TIME_BUDGET_MARKERS: Final[tuple[str, ...]] = (
    "[DEADLINE REACHED]",
    "deadline exceeded",
    "budget exhausted",
    "deadline exhausted",
    "duration guard failed",
)
_MEASURED_OVER_LIMIT = r"measured \d+(?:\.\d+)?\s*ms at or above \d+(?:\.\d+)?\s*ms"
_TIME_BUDGET_MARKER = re.compile(
    "|".join((*(re.escape(marker) for marker in TIME_BUDGET_MARKERS), _MEASURED_OVER_LIMIT)),
    re.IGNORECASE,
)
_CORRECTNESS_FAILURE = re.compile(
    "|".join(
        (
            r"^\s*\[verify\]\s+FAILED:",
            r"^\s*(?:--- )?FAIL(?:ED)?\b",
            r"^\s*ERROR\b",
            r"failed while running:",
            r"\b[1-9]\d* (?:failed|failures?|errors?|offenses?)\b",
            r":\d+(?::\d+)?: error\b",
            r"\berror TS\d+",
        )
    ),
    re.IGNORECASE,
)
_VOLATILE_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SHELL_DIAGNOSTIC = re.compile(r"^(?:/bin/)?sh: .*(?:syntax error|not found|unexpected)", re.I)
_INVOCATION_EXIT_CODES: Final[frozenset[int]] = frozenset({2, 127})
_NO_SLOT_EXIT_CODE: Final[int] = -1
_SYNTAX_CHECK_TIMEOUT_SECONDS: Final[float] = 10.0
_INVOCATION_EXIT_CODE: Final[int] = 2
_SIGNATURE_FALLBACK_LINES: Final[int] = 5
_DIGEST_HALF_LINES: Final[int] = 100
_DIGEST_HALF_CHARS: Final[int] = 10000
_DIGEST_LINE_CHARS: Final[int] = 400
_DIGEST_LINE = re.compile(
    "|".join(
        (
            _CORRECTNESS_FAILURE.pattern,
            _TIME_BUDGET_MARKER.pattern,
            _CANCELLED_MARKER.pattern,
            r"\bError:",
        )
    ),
    re.IGNORECASE | re.MULTILINE,
)


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
    inconclusive_runs: int = 0
    invocation_error: str = ""
    failure_digest: str = ""
    pass_seconds: float = 0.0

    @property
    def failure_evidence(self) -> str:
        return f"{self.failure_digest}\n{self.output_tail}"

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def _interrupted(self) -> bool:
        return self.exit_code < 0 or _CANCELLED_MARKER.search(self.failure_evidence) is not None

    @property
    def time_only(self) -> bool:
        if (
            self.passed
            or self.timed_out
            or self.invocation_error
            or _shows_correctness_failure(self.failure_evidence)
        ):
            return False
        return _TIME_BUDGET_MARKER.search(self.failure_evidence) is not None or (
            self._interrupted and self.inconclusive_runs >= MAX_INCONCLUSIVE_GATE_RUNS
        )

    @property
    def inconclusive(self) -> bool:
        if self.passed or self.timed_out or self.time_only:
            return False
        return self._interrupted and self.inconclusive_runs < MAX_INCONCLUSIVE_GATE_RUNS

    @property
    def block(self) -> PublicationBlock | None:
        if self.passed or self.time_only:
            return None
        return PublicationBlock.GATE_TIMED_OUT if self.timed_out else PublicationBlock.GATE_FAILED

    @property
    def routes_back_to_development(self) -> bool:
        return (
            self.block is not None
            and not self.inconclusive
            and not self.invocation_error
            and self.route_backs < MAX_PUBLICATION_GATE_FAILURES
        )

    def feedback(self) -> str:
        verdict = "timed out" if self.timed_out else f"failed (exit {self.exit_code})"
        summary = (
            f"Failure summary:\n{self.failure_digest}"
            if self.failure_digest
            else f"No failure lines were found in the gate output (exit {self.exit_code}); "
            "read the output tail for the cause."
        )
        hang_hint = (
            " A gate that runs this far past its own last passing time almost always means "
            "a test is hung or crash-looping: find the step that never finished in the "
            "output tail, fix it, and give the suite a runner-level timeout that kills a "
            "hung test (testing-policy.md rule 8)."
            if self.timed_out
            else ""
        )
        return (
            f"Publication gate `{self.command}` {verdict} on tree {self.tree_sha}; the run's "
            f"commits already landed. Fix the failures below so the gate passes.{hang_hint}\n"
            f"{summary}\n\nOutput tail:\n{self.output_tail}"
        )


def _failure_digest(output: str) -> str:
    return failure_line_digest(
        output,
        _DIGEST_LINE,
        half_lines=_DIGEST_HALF_LINES,
        half_chars=_DIGEST_HALF_CHARS,
        line_chars=_DIGEST_LINE_CHARS,
    )


def _shows_correctness_failure(output: str) -> bool:
    return any(
        _CORRECTNESS_FAILURE.search(line) and not _TIME_BUDGET_MARKER.search(line)
        for line in output.splitlines()
    )


def _warn_time_only(result: PublicationGateResult) -> None:
    markers = sorted(
        {match.group(0) for match in _TIME_BUDGET_MARKER.finditer(result.failure_evidence)}
    )
    cause = (
        ", ".join(repr(marker) for marker in markers)
        if markers
        else f"{result.inconclusive_runs} inconclusive runs"
    )
    logger.warning(
        "Publication gate `{}` on tree {} failed on time only ({}), with no correctness "
        "failure; landing the commits with this time-only warning",
        result.command,
        result.tree_sha,
        cause,
    )


def gate_failure_signature(
    exit_code: int, *, timed_out: bool, output: str, digest: str = ""
) -> str:
    steps = sorted(
        {
            match.expand(r"\1").strip()
            for marker in _FAILED_STEP_MARKERS
            for match in marker.finditer(f"{digest}\n{output}")
        }
    )
    if steps:
        basis = "\n".join(steps)
    else:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        evidence = digest.splitlines() or lines[-_SIGNATURE_FALLBACK_LINES:]
        tail = [_VOLATILE_NUMBER.sub("#", line) for line in evidence]
        basis = "\n".join((f"exit {exit_code} timed out {timed_out}", *tail))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


GateRunner = Callable[[str, Path, float], PublicationGateRun]
TreeLookup = Callable[[Path, str], str]


def publication_gate_record_path(root: Path) -> Path:
    return root / _RECORD_PATH


_PARSEABLE_COMMANDS: deque[str] = deque(maxlen=64)
_UNPARSEABLE_COMMANDS: deque[str] = deque(maxlen=64)
_WARNED_PROSE: deque[tuple[str, str]] = deque(maxlen=64)


def _parses_as_shell(command: str) -> bool:
    if command in _PARSEABLE_COMMANDS:
        return True
    if command in _UNPARSEABLE_COMMANDS:
        return False
    try:
        verdict = (
            run_process(
                "sh",
                ("-n", "-c", command),
                options=ProcessRunOptions(
                    timeout=_SYNTAX_CHECK_TIMEOUT_SECONDS, label="publication-gate-syntax"
                ),
            ).returncode
            == 0
        )
    except ProcessExecutionError:
        return False
    (_PARSEABLE_COMMANDS if verdict else _UNPARSEABLE_COMMANDS).append(command)
    return verdict


def _warn_dropped_prose(source: str, command: str, prose: str) -> None:
    if (source, prose) in _WARNED_PROSE:
        return
    _WARNED_PROSE.append((source, prose))
    logger.warning(
        "Publication gate declared at {} mixes a command with prose; running `{}` and dropped {!r}",
        source,
        command,
        prose,
    )


def _resolve_declared_command(value: str, source: str) -> GateDeclaration:
    quoted = _BACKTICK_COMMAND.search(value)
    if quoted:
        return GateDeclaration(value, source, as_str(quoted.group(1), field="gate command").strip())
    if _parses_as_shell(value):
        return GateDeclaration(value, source, value)
    head = value.split(";", 1)[0].strip()
    if head and head != value and _parses_as_shell(head):
        _warn_dropped_prose(source, head, value[len(head) :].lstrip("; "))
        return GateDeclaration(value, source, head)
    return GateDeclaration(
        value, source, None, f"`{value}` is not a shell command the gate can run"
    )


def resolve_publication_gate(root: Path) -> GateDeclaration | None:
    policy = root / CANONICAL_DIR / _GATE_POLICY_FILE
    try:
        lines = policy.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    prefix = f"{FACT_MARKER} {_GATE_FACT_KEY}:"
    for number, line in enumerate(lines, start=1):
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        if not value or value.startswith(_UNRESOLVED_FACT):
            return None
        return _resolve_declared_command(value, f"{policy}:{number}")
    return None


def declared_publication_gate(root: Path) -> str | None:
    declaration = resolve_publication_gate(root)
    return declaration.gate_key if declaration is not None else None


def _invocation_error(run: PublicationGateRun) -> str:
    if run.timed_out or run.exit_code not in _INVOCATION_EXIT_CODES:
        return ""
    lines = [line.strip() for line in run.output.splitlines() if line.strip()]
    if lines and all(_SHELL_DIAGNOSTIC.search(line) for line in lines):
        return f"the gate command never started (exit {run.exit_code}): {lines[-1]}"
    return ""


def _run_gate_command(command: str, cwd: Path, timeout_seconds: float) -> PublicationGateRun:
    started = time.monotonic()
    run = _run_gate_process(command, cwd, timeout_seconds)
    return replace(run, elapsed_seconds=time.monotonic() - started)


def _run_gate_process(command: str, cwd: Path, timeout_seconds: float) -> PublicationGateRun:
    with reaped_process_scope("gate", owner_pid=os.getpid()) as scope_env:
        try:
            result = run_process(
                "sh",
                ("-c", command),
                options=ProcessRunOptions(
                    cwd=cwd, env=scope_env, timeout=timeout_seconds, label="publication-gate"
                ),
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
        signature = fields.get("failure_signature", "")
        invocation_error = fields.get("invocation_error", "")
        digest = fields.get("failure_digest", "")
        if (
            not isinstance(output_tail, str)
            or not isinstance(signature, str)
            or not isinstance(invocation_error, str)
            or not isinstance(digest, str)
        ):
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
            failure_signature=signature,
            inconclusive_runs=as_int(fields.get("inconclusive_runs", 0), field="inconclusive_runs"),
            invocation_error=invocation_error,
            failure_digest=digest,
            pass_seconds=_as_seconds(fields.get("pass_seconds", 0.0)),
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
        "inconclusive_runs": result.inconclusive_runs,
        "invocation_error": result.invocation_error,
        "failure_digest": result.failure_digest,
        "time_only": result.time_only,
        "pass_seconds": result.pass_seconds,
    }


def _as_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return max(0.0, float(value))


def gate_timeout_seconds(previous: PublicationGateResult | None) -> float:
    if previous is None or previous.pass_seconds <= 0:
        return PUBLICATION_GATE_TIMEOUT_SECONDS
    scaled = previous.pass_seconds * PASS_TIME_TIMEOUT_MULTIPLIER
    return min(PUBLICATION_GATE_TIMEOUT_SECONDS, max(MIN_PUBLICATION_GATE_TIMEOUT_SECONDS, scaled))


def check_publication_gate(
    root: Path,
    feature_sha: str,
    *,
    timeout_seconds: float | None = None,
    runner: GateRunner | None = None,
    tree_of: TreeLookup | None = None,
) -> PublicationGateResult | None:
    declaration = resolve_publication_gate(root)
    if declaration is None:
        return None
    command = declaration.gate_key
    tree_sha = (tree_of or _tree_of)(root, feature_sha)
    previous = _load_record(root)
    if (
        previous is not None
        and tree_sha
        and previous.tree_sha == tree_sha
        and previous.command == command
        and not previous.inconclusive
    ):
        return previous
    if declaration.command is None:
        return _record_misconfigured_gate(root, declaration, tree_sha, previous)
    if timeout_seconds is None:
        timeout_seconds = gate_timeout_seconds(previous)
    logger.info(
        "Running the publication gate `{}` on tree {} (stopped after {:.0f}s)",
        command,
        tree_sha,
        timeout_seconds,
    )
    if runner is not None:
        run = runner(command, root, timeout_seconds)
    else:
        with publication_gate_slot() as acquired:
            if not acquired:
                return replace(
                    PublicationGateResult(
                        command=command,
                        tree_sha=tree_sha,
                        exit_code=_NO_SLOT_EXIT_CODE,
                        timed_out=False,
                        output_tail="no host-wide publication gate slot became free in time",
                    ),
                    route_backs=previous.route_backs if previous is not None else 0,
                )
            run = _run_gate_command(command, root, timeout_seconds)
    invocation_error = _invocation_error(run)
    if invocation_error:
        logger.error(
            "Publication gate `{}` declared at {} could not run: {}; this is a policy error, "
            "not a code failure",
            command,
            declaration.source,
            invocation_error,
        )
    result = PublicationGateResult(
        command=command,
        tree_sha=tree_sha,
        exit_code=run.exit_code,
        timed_out=run.timed_out,
        output_tail=run.output[-_OUTPUT_TAIL_CHARS:],
        failure_digest=_failure_digest(run.output),
    )
    result = replace(
        result,
        pass_seconds=(
            run.elapsed_seconds
            if result.passed and run.elapsed_seconds > 0
            else (previous.pass_seconds if previous is not None else 0.0)
        ),
    )
    if result.inconclusive:
        return _record_inconclusive_run(root, result, previous)
    cleared = result.passed or result.time_only
    failures = 0 if cleared else (previous.consecutive_failures if previous else 0) + 1
    signature = (
        ""
        if cleared
        else gate_failure_signature(
            result.exit_code,
            timed_out=result.timed_out,
            output=result.output_tail,
            digest=result.failure_digest,
        )
    )
    same_failure = previous is not None and previous.failure_signature == signature
    route_backs = previous.route_backs if same_failure and previous is not None else 0
    result = replace(
        result,
        consecutive_failures=failures,
        route_backs=0 if cleared else route_backs,
        failure_signature=signature,
        invocation_error=invocation_error,
    )
    if result.time_only:
        _warn_time_only(result)
    elif not result.passed:
        logger.warning(
            "Publication gate `{}` is red on tree {} (exit {}, timed out: {}); the gate is "
            "advisory, so the commits land and its failure routes back to development",
            command,
            tree_sha,
            result.exit_code,
            result.timed_out,
        )
    _save_record(root, result)
    return result


def _record_inconclusive_run(
    root: Path, result: PublicationGateResult, previous: PublicationGateResult | None
) -> PublicationGateResult:
    same_tree = previous is not None and previous.tree_sha == result.tree_sha
    runs = (previous.inconclusive_runs if same_tree and previous is not None else 0) + 1
    recorded = replace(
        result,
        consecutive_failures=previous.consecutive_failures if previous is not None else 0,
        route_backs=previous.route_backs if previous is not None else 0,
        failure_signature=previous.failure_signature if previous is not None else "",
        inconclusive_runs=runs,
    )
    if recorded.time_only:
        _warn_time_only(recorded)
    else:
        logger.warning(
            "Publication gate `{}` on tree {} was stopped by a signal (exit {}); the result is "
            "inconclusive, so the commits stay unpublished and the gate re-runs on the next "
            "landing ({} of {} inconclusive runs on this tree)",
            result.command,
            result.tree_sha,
            result.exit_code,
            runs,
            MAX_INCONCLUSIVE_GATE_RUNS,
        )
    _save_record(root, recorded)
    return recorded


def _record_misconfigured_gate(
    root: Path,
    declaration: GateDeclaration,
    tree_sha: str,
    previous: PublicationGateResult | None,
) -> PublicationGateResult:
    problem = (
        f"publication gate declared at {declaration.source} is misconfigured: {declaration.problem}"
    )
    if previous is None or previous.invocation_error != problem:
        logger.error(
            "{}; the run's commits stay unpublished until the policy declares a runnable command",
            problem,
        )
    result = PublicationGateResult(
        command=declaration.gate_key,
        tree_sha=tree_sha,
        exit_code=_INVOCATION_EXIT_CODE,
        timed_out=False,
        output_tail=problem,
        invocation_error=problem,
    )
    _save_record(root, result)
    return result


def last_publication_gate_result(root: Path) -> PublicationGateResult | None:
    return _load_record(root)


def red_publication_gate(
    root: Path, feature_sha: str = "HEAD", *, tree_of: TreeLookup | None = None
) -> PublicationGateResult | None:
    record = _load_record(root)
    if record is None or record.block is None or record.inconclusive:
        return None
    if declared_publication_gate(root) != record.command:
        return None
    tree_sha = (tree_of or _tree_of)(root, feature_sha)
    return record if tree_sha and tree_sha == record.tree_sha else None


def commit_tree(root: Path, commit: str) -> str:
    return _tree_of(root, commit)


def run_advisory_publication_gate(
    root: Path, feature_sha: str, *, published_tree: str = ""
) -> PublicationGateResult | None:
    try:
        if published_tree and _tree_of(root, feature_sha) == published_tree:
            logger.info(
                "Publication gate skipped: {} adds nothing to the tree its target already published",
                feature_sha,
            )
            return None
        return check_publication_gate(root, feature_sha)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Publication gate could not run on {}: {}", feature_sha, exc)
        return None


def publication_gate_feedback(
    root: Path, policy: PipelinePolicy, commit_phase: str
) -> PublicationGateFailed | None:
    loop_phase = publication_gate_loop_phase(policy, commit_phase)
    if loop_phase is None:
        leave_publication_gate_feedback(root, policy)
        return None
    result = _load_record(root)
    if result is None or not result.routes_back_to_development:
        return None
    _save_record(root, replace(result, route_backs=result.route_backs + 1))
    write_loop_feedback(root, policy, loop_phase, result.feedback())
    return PublicationGateFailed(phase=commit_phase, detail=result.feedback())


def write_loop_feedback(root: Path, policy: PipelinePolicy, loop_phase: str, feedback: str) -> bool:
    loop_target = policy.phases[loop_phase].transitions.on_loopback
    if not loop_target:
        return False
    from ralph.phases.required_artifacts import retry_hint_path

    hint = root / retry_hint_path(loop_target, pipeline_policy=policy)
    hint.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write_text_if_changed(
        DEFAULT_FILE_BACKEND, hint, feedback, tmp_path=hint.with_suffix(".tmp")
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
    if loop_phase is None or not write_loop_feedback(root, policy, loop_phase, result.feedback()):
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
    if definition is None or definition.role != "commit":
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
    "MAX_INCONCLUSIVE_GATE_RUNS",
    "MAX_PUBLICATION_GATE_FAILURES",
    "MIN_PUBLICATION_GATE_TIMEOUT_SECONDS",
    "PASS_TIME_TIMEOUT_MULTIPLIER",
    "PUBLICATION_GATE_TIMEOUT_SECONDS",
    "TIME_BUDGET_MARKERS",
    "GateDeclaration",
    "GateRunner",
    "PublicationBlock",
    "PublicationGateFailed",
    "PublicationGateResult",
    "PublicationGateRun",
    "TreeLookup",
    "check_publication_gate",
    "commit_tree",
    "declared_publication_gate",
    "gate_failure_signature",
    "gate_timeout_seconds",
    "last_publication_gate_result",
    "leave_publication_gate_feedback",
    "publication_gate_feedback",
    "publication_gate_loop_phase",
    "publication_gate_record_path",
    "red_publication_gate",
    "resolve_publication_gate",
    "run_advisory_publication_gate",
    "write_loop_feedback",
]
