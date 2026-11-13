from __future__ import annotations

import io
import json
import sys
import tempfile
import tokenize
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.executor._process_run_options import ProcessRunOptions
from ralph.executor.process import TIMEOUT_EXIT_CODE, run_process
from ralph.process._spawn_env import sanitize_process_environment
from ralph.testing._audit_baseline import head_tree
from ralph.testing._audit_content_set import read_tree

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "BANDIT_TIMEOUT_SECONDS",
    "FIX_HINTS",
    "SAST_CONFIG_FILENAME",
    "SCANNED_PREFIX",
    "SCANNED_SUFFIX",
    "WAIVER_INSTRUCTION",
    "SastFinding",
    "audit_sast_roots",
    "diff_findings",
    "main",
    "scan_files",
]

SCANNED_PREFIX = "ralph/"

SCANNED_SUFFIX = ".py"

BANDIT_TIMEOUT_SECONDS = 20.0

MAX_CLI_ROOT_ARGUMENTS = 2

SAST_CONFIG_FILENAME = "pyproject.toml"

SAST_CONFIG_SECTION = "tool.bandit"

WAIVER_INSTRUCTION = (
    "A false positive is waived with a skips entry for the finding id in the "
    f"[{SAST_CONFIG_SECTION}] table of {SAST_CONFIG_FILENAME}, and the waiver is "
    "recorded with a reason, an owner and a review date under the security "
    "policy Exceptions section. A source comment never waives a finding."
)

FIX_HINTS: dict[str, str] = {
    "B101": "replace the assert with an explicit raise, or move the check into a test",
    "B102": "remove the exec() call and dispatch on an explicit mapping instead",
    "B103": "set the file mode explicitly to the restrictive value the code needs",
    "B104": "bind to the explicit loopback host instead of every interface",
    "B105": "read the secret from the environment or a secret manager instead of inlining it",
    "B106": "read the secret from the environment or a secret manager instead of inlining it",
    "B107": "read the secret from the environment or a secret manager instead of inlining it",
    "B108": "use tempfile.mkdtemp() or NamedTemporaryFile instead of a fixed directory",
    "B110": "handle the exception explicitly (log it or re-raise it) instead of passing",
    "B111": "handle the exception explicitly (log it or re-raise it) instead of passing",
    "B112": "handle the exception explicitly (log it or re-raise it) instead of continuing",
    "B113": "request the request with an explicit timeout instead of an unbounded call",
    "B201": "return a copy of the mutable argument instead of the argument itself",
    "B301": "replace the pickle payload with a safe format such as JSON",
    "B303": "use hashlib.sha256 instead of md5 for integrity hashing",
    "B304": "use tempfile.mkstemp() or NamedTemporaryFile instead of mktemp",
    "B307": "remove the eval() call and dispatch on an explicit mapping instead",
    "B310": "open the URL through a client that enforces a timeout and a scheme check",
    "B311": "use secrets or os.urandom for the security-relevant random value",
    "B312": "remove the telnet usage and speak SSH instead",
    "B313-B320": "parse the XML with defusedxml instead of the standard library",
    "B321": "use an explicit TLS session for the FTP transfer",
    "B323": "verify the TLS certificate instead of trusting an unverified context",
    "B324": "use hashlib.sha256 instead of md5 or sha1 for integrity hashing",
    "B325": "use tempfile.NamedTemporaryFile instead of tempnam",
    "B401": "import the module inside the narrowest scope that needs it",
    "B404": "route the child process through the typed process seam of the package",
    "B602": "pass an argument vector with shell=False instead of a shell string",
    "B603": "pass an explicit argument vector and check the return code",
    "B604": "pass shell=False explicitly together with an argument vector",
    "B605": "pass an argument vector and never shell=True",
    "B606": "pass shell=False explicitly",
    "B607": "pass the absolute executable path instead of a partial one",
    "B608": "use a parameterised query instead of an interpolated statement",
    "B609": "bind the wildcard address only where the exposure is intended",
    "B610": "validate the URL against an allowlist before requesting it",
    "B611": "validate the input against an allowlist before using it in a SQL statement",
    "B701": "enable autoescape on the Jinja environment that renders the template",
    "B702": "enable autoescape on the Jinja environment that renders the template",
}

GENERIC_FIX_HINT = (
    "read the Bandit documentation entry for this test id and fix the cause. " + WAIVER_INSTRUCTION
)


@dataclass(frozen=True)
class SastFinding:
    file_path: str
    line: int
    test_id: str
    test_name: str
    severity: str
    message: str
    fix_hint: str
    code_line: str

    def __str__(self) -> str:
        return (
            f"{self.file_path}:{self.line}: [{self.test_id}] {self.test_name}: "
            f"{self.message} | fix: {self.fix_hint}"
        )


def _fix_hint(test_id: str) -> str:
    return FIX_HINTS.get(test_id, GENERIC_FIX_HINT)


def _package_python_sources(root: Path) -> dict[str, str]:
    return {
        path: text
        for path, text in read_tree(root).items()
        if path.startswith(SCANNED_PREFIX) and path.endswith(SCANNED_SUFFIX)
    }


def _changed_paths(current: Mapping[str, str], baseline: Mapping[str, str]) -> list[str]:
    return sorted(
        path for path in set(current) | set(baseline) if current.get(path) != baseline.get(path)
    )


def _source_line(sources: Mapping[str, str], path: str, line: int) -> str:
    lines = sources.get(path, "").splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _normalise_filename(filename: str) -> str:
    cleaned = filename.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def _parse_results(stdout: str, sources: Mapping[str, str]) -> list[SastFinding]:
    decoded: object = json.loads(stdout)
    if not isinstance(decoded, dict):
        msg = "the Bandit report is not a JSON object"
        raise RuntimeError(msg)
    payload: dict[str, object] = decoded
    results = payload.get("results")
    if not isinstance(results, list):
        msg = "the Bandit report carries no 'results' list"
        raise RuntimeError(msg)
    findings: list[SastFinding] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        path = _normalise_filename(str(item.get("filename", "")))
        test_id = str(item.get("test_id", ""))
        raw_line = item.get("line_number")
        line = raw_line if isinstance(raw_line, int) else 0
        findings.append(
            SastFinding(
                file_path=path,
                line=line,
                test_id=test_id,
                test_name=str(item.get("test_name", "")),
                severity=str(item.get("issue_severity", "")),
                message=str(item.get("issue_text", "")),
                fix_hint=_fix_hint(test_id),
                code_line=_source_line(sources, path, line),
            )
        )
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.test_id))
    return findings


def _comment_free_source(text: str) -> str:
    lines = text.split("\n")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return text
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        (row, column), (_, end_column) = token.start, token.end
        index = row - 1
        if index < 0 or index >= len(lines):
            continue
        line = lines[index]
        lines[index] = line[:column] + " " * (end_column - column) + line[end_column:]
    return "\n".join(lines)


def _write_comment_free_tree(
    destination: Path,
    relative_paths: Sequence[str],
    sources: Mapping[str, str],
) -> None:
    for relative in relative_paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_comment_free_source(sources[relative]), encoding="utf-8")


def _sast_config_path(root: Path) -> Path | None:
    candidate = root / SAST_CONFIG_FILENAME
    if not candidate.is_file():
        return None
    return candidate.resolve()


def scan_files(
    root: Path, relative_paths: Sequence[str], sources: Mapping[str, str]
) -> list[SastFinding]:
    if not relative_paths:
        return []
    command = [
        sys.executable,
        "-m",
        "bandit",
        "-f",
        "json",
        "-q",
        "--exit-zero",
    ]
    config_path = _sast_config_path(root)
    if config_path is not None:
        command.extend(["-c", str(config_path)])
    command.extend(["--", *relative_paths])
    with tempfile.TemporaryDirectory(prefix="ralph-sast-scan-") as directory:
        scan_root = Path(directory)
        _write_comment_free_tree(scan_root, relative_paths, sources)
        completed = run_process(
            command[0],
            command[1:],
            options=ProcessRunOptions(
                cwd=scan_root,
                timeout=BANDIT_TIMEOUT_SECONDS,
                capture_output=True,
                label="testing:audit-sast",
            ),
        )
    if completed.returncode == TIMEOUT_EXIT_CODE:
        msg = f"the Bandit scan exceeded {BANDIT_TIMEOUT_SECONDS:.0f}s"
        raise RuntimeError(msg)
    stdout = completed.stdout.strip()
    if not stdout:
        msg = f"Bandit produced no report (exit {completed.returncode}): {completed.stderr.strip()}"
        raise RuntimeError(msg)
    try:
        return _parse_results(stdout, sources)
    except ValueError as exc:
        msg = f"the Bandit report is not readable JSON: {exc}"
        raise RuntimeError(msg) from exc


def _finding_key(finding: SastFinding) -> tuple[str, str, str]:
    return (finding.file_path, finding.test_id, finding.code_line)


def _broad_key(finding: SastFinding) -> tuple[str, str]:
    return (finding.file_path, finding.test_id)


def diff_findings(
    baseline: Sequence[SastFinding],
    current: Sequence[SastFinding],
) -> tuple[list[SastFinding], int]:
    exact_available = Counter(_finding_key(finding) for finding in baseline)
    broad_available = Counter(_broad_key(finding) for finding in baseline)
    total_baseline = sum(broad_available.values())

    unmatched: list[SastFinding] = []
    for finding in current:
        key = _finding_key(finding)
        wide = _broad_key(finding)
        if exact_available[key] > 0:
            exact_available[key] -= 1
            broad_available[wide] -= 1
        else:
            unmatched.append(finding)

    new: list[SastFinding] = []
    for finding in unmatched:
        wide = _broad_key(finding)
        if broad_available[wide] > 0:
            broad_available[wide] -= 1
        else:
            new.append(finding)

    return new, total_baseline


def audit_sast_roots(
    baseline_root: Path | None,
    current_root: Path,
) -> tuple[list[SastFinding], int]:
    current_sources = _package_python_sources(current_root)
    baseline_sources = _package_python_sources(baseline_root) if baseline_root is not None else {}
    changed = _changed_paths(current_sources, baseline_sources)
    current_findings = scan_files(
        current_root,
        [path for path in changed if path in current_sources],
        current_sources,
    )
    baseline_findings: list[SastFinding] = []
    if baseline_root is not None:
        baseline_findings = scan_files(
            baseline_root,
            [path for path in changed if path in baseline_sources],
            baseline_sources,
        )
    return diff_findings(baseline_findings, current_findings)


def _default_root() -> Path:
    return Path(__file__).parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    sanitize_process_environment()
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > MAX_CLI_ROOT_ARGUMENTS:
        print(
            "Usage: python -m ralph.testing.audit_sast [baseline_root] [current_root]",
            file=sys.stderr,
        )
        return 2
    baseline_root: Path | None
    if len(args) == MAX_CLI_ROOT_ARGUMENTS:
        baseline_root = Path(args[0])
        current_root = Path(args[1])
    elif len(args) == 1:
        baseline_root = None
        current_root = Path(args[0])
    else:
        current_root = _default_root()
        baseline_root = head_tree(current_root)
        if baseline_root is None:
            print(
                "Error: no committed baseline is available for this package; "
                "pass <baseline_root> <current_root> explicitly.",
                file=sys.stderr,
            )
            return 2
    for root in (baseline_root, current_root):
        if root is not None and not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2

    baseline_label = str(baseline_root) if baseline_root is not None else "(empty baseline)"
    print(
        f"Auditing static analysis (baseline diff): baseline={baseline_label} current={current_root}"
    )
    config_path = _sast_config_path(current_root)
    config_label = (
        f"{config_path} [{SAST_CONFIG_SECTION}]" if config_path is not None else "Bandit defaults"
    )
    print(f"SAST configuration: {config_label}")
    print()

    try:
        new_findings, baseline_count = audit_sast_roots(baseline_root, current_root)
    except RuntimeError as exc:
        print(f"Error: the static-analysis gate could not run: {exc}", file=sys.stderr)
        return 2

    if new_findings:
        print(
            f"SAST FINDINGS: {len(new_findings)} new finding(s); "
            f"{baseline_count} pre-existing finding(s) in the baseline"
        )
        print("=" * 72)
        for finding in new_findings:
            print(f"  {finding}")
        print()
        print(
            "A NEW static-analysis finding fails the gate: fix the cause at the "
            "reported location. " + WAIVER_INSTRUCTION
        )
        return 1

    print(f"No new static-analysis findings ({baseline_count} pre-existing finding(s)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
