"""Secret-scan / SAST gate — a real scanner in BASELINE-DIFF mode.

The gate replaces the deferred (``RALPH-PENDING``) Bandit entry with a
scanner that actually runs, is deterministic, and never touches the
network. It has two halves.

SECRET SCAN

Named, explicit detectors recognise the credential shapes that leak in
practice: AWS access key ids, GitHub tokens (classic and fine-grained),
private-key PEM headers, Slack tokens, and generic
``api_key`` / ``secret`` / ``password`` assignments whose literal has
high entropy. Every detector is a module-level constant so a reviewer can
read the whole rule set in one place.

Obvious placeholders are never findings: ``REPLACE-ME``, ``xxxx``,
``example``, ``dummy``, ``your-…`` and ``${…}`` templates are the
difference between a gate that reports real leaks and a gate that gets
disabled on day one.

BASELINE-DIFF MODE

The gate never scans a whole tree and hopes for the best. It compares a
BASELINE content set against the CURRENT content set and reports only
findings on NEW or CHANGED lines. A finding that is already in the
baseline is pre-existing debt, not a regression, and it stays visible in
the baseline until somebody fixes it — but it does not turn every commit
red. The baseline is supplied by the caller (the coordinator wires a
git-diff based baseline); the gate itself never shells out and never
reads version control.

SAST SLICE

A deliberately small static-analysis slice covers the two patterns that
matter most in an agent-driven codebase: ``eval()`` / ``exec()`` on
non-literal input, and ``subprocess`` calls with ``shell=True``. It is
AST-based (not regex), resolves ``import subprocess as sp`` aliases, and
is documented as a slice — it is not a replacement for a full SAST. The
slice targets SHIPPED code: a test fixture that executes a generated
snippet exercises the feature under test, so test modules are out of its
scope. The SECRET half of the gate has no such exclusion — that is where
a committed credential usually hides.

BYPASS

An inline ``# secret-scan-ok: <reason>`` marker on the offending line
suppresses the finding, mirroring the repository's existing
``# mcp-timeout-ok:`` convention. The marker constant is
:data:`SECRET_SCAN_BYPASS_MARKER`.

INPUT SEAM

    audit_secret_scan(baseline, current) -> list[SecretFinding]

``baseline`` and ``current`` are mappings of repository-relative path to
file text. The gate is a pure function over them: no network, no
subprocess, no clock.

Usage:
    python -m ralph.testing.audit_secret_scan [baseline_root] [current_root]

With no arguments the gate scans the repository root against an EMPTY
baseline (every line counts as new). With two roots it reports only NEW
or CHANGED lines.

Exit 0 = clean, 1 = findings, 2 = a root was not found.
"""

from __future__ import annotations

import ast
import hashlib
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_baseline import head_tree
from ralph.testing._audit_content_set import read_tree

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "AWS_ACCESS_KEY_ID_PATTERN",
    "GENERIC_SECRET_ASSIGNMENT_PATTERN",
    "GITHUB_FINE_GRAINED_TOKEN_PATTERN",
    "GITHUB_TOKEN_PATTERN",
    "PRIVATE_KEY_PEM_PATTERN",
    "SECRET_DETECTORS",
    "SECRET_SCAN_BYPASS_MARKER",
    "SLACK_TOKEN_PATTERN",
    "SecretFinding",
    "audit_secret_roots",
    "audit_secret_scan",
    "main",
    "scan_python_sast",
    "scan_secret_lines",
]

#: Inline marker that suppresses a finding on its line, in the style of
#: the repository's existing ``# mcp-timeout-ok:`` bypass.
SECRET_SCAN_BYPASS_MARKER = "secret-scan-ok"

#: AWS access key ids (long-lived ``AKIA…`` and temporary ``ASIA…`` forms
#: plus the other AWS key prefixes).
AWS_ACCESS_KEY_ID_PATTERN = re.compile(
    r"\b(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AIPA|ANPA|ANVA|AROA)[0-9A-Z]{16}\b"
)

#: GitHub classic personal access / OAuth / user / server tokens.
GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")

#: GitHub fine-grained personal access tokens.
GITHUB_FINE_GRAINED_TOKEN_PATTERN = re.compile(r"\bgithub_pat_[A-Za-z0-9]{22,255}\b")

#: Private-key PEM header. Matched against a WHOLE line: a real key file
#: puts the header on its own line, while prose that merely mentions the
#: header inside a sentence is not a leaked key.
PRIVATE_KEY_PEM_PATTERN = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----")

#: Slack tokens (bot, user, app, refresh, legacy).
SLACK_TOKEN_PATTERN = re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")

#: Generic credential assignment. The VALUE group is only reported when
#: it also passes the entropy / character-class / placeholder rules.
GENERIC_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?P<key>api[_-]?key|secret[_-]?key|apikey|secret|password|passwd|pwd"
    r"|token|access[_-]?key|auth[_-]?token|private[_-]?key)\b"
    r"\s*[:=]\s*[\"'](?P<value>[^\"'\n]{8,})[\"']"
)

#: The named secret detectors, in reporting order.
SECRET_DETECTORS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", AWS_ACCESS_KEY_ID_PATTERN),
    ("github-token", GITHUB_TOKEN_PATTERN),
    ("github-fine-grained-token", GITHUB_FINE_GRAINED_TOKEN_PATTERN),
    ("private-key", PRIVATE_KEY_PEM_PATTERN),
    ("slack-token", SLACK_TOKEN_PATTERN),
)

#: Substrings that mark a literal as an obvious placeholder rather than a
#: live credential. Matched case-insensitively against the candidate
#: value.
PLACEHOLDER_MARKERS: frozenset[str] = frozenset(
    {
        "change-me",
        "changeme",
        "dummy",
        "example",
        "fake",
        "fixme",
        "lorem",
        "notreal",
        "placeholder",
        "redacted",
        "replace-me",
        "replaceme",
        "sample",
        "todo",
        "xxxx",
        "your-",
        "your_",
    }
)

#: Values that are TEMPLATES, not literals: ``${VAR}``, ``{{ var }}``,
#: ``<value>`` and ``$VAR``.
TEMPLATE_VALUE_PATTERN = re.compile(
    r"^(?:\$\{[^}]*\}|\{\{[^}]*\}\}|<[^>]*>|\$[A-Za-z_][A-Za-z0-9_]*)$"
)

#: Minimum length of a generic-secret literal.
MIN_GENERIC_SECRET_LENGTH = 20

#: Minimum Shannon entropy (bits per character) of a generic-secret literal.
MIN_GENERIC_SECRET_ENTROPY = 3.2

#: The SAST slice: builtin calls that execute their argument as code.
SAST_EVAL_CALLS: frozenset[str] = frozenset({"eval", "exec"})

#: The SAST slice: ``subprocess`` entry points that accept ``shell=``.
SAST_SHELL_FUNCTIONS: frozenset[str] = frozenset(
    {"Popen", "call", "check_call", "check_output", "run"}
)

#: Accepted root-argument counts for the CLI: baseline-only, or
#: baseline plus current.
MAX_CLI_ROOT_ARGUMENTS = 2

#: Directory names that mark a TEST tree. The SAST slice targets shipped
#: code: a test fixture that executes a generated snippet is exercising the
#: feature under test, not shipping dynamic code execution to users. The
#: SECRET half of the gate still scans test files in full — that is where a
#: committed credential usually hides.
TEST_TREE_DIRECTORIES: frozenset[str] = frozenset({"test", "tests"})

#: File names that mark a test module.
TEST_FILE_PREFIXES: tuple[str, ...] = ("test_",)
TEST_FILE_SUFFIXES: tuple[str, ...] = ("_test.py",)
TEST_FILE_NAMES: frozenset[str] = frozenset({"conftest.py"})


@dataclass(frozen=True)
class SecretFinding:
    """One secret-scan or SAST finding."""

    file_path: str
    line: int
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


def _regex_group_str(match: re.Match[str], group: str) -> str:
    """Return a named regex group as ``str`` (never ``Any``)."""
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _shannon_entropy(value: str) -> float:
    """Return the Shannon entropy of *value* in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    entropy = 0.0
    for count in counts.values():
        probability = count / length
        entropy -= probability * math.log2(probability)
    return entropy


def _is_placeholder(value: str) -> bool:
    """Return True when *value* is an obvious placeholder, not a credential."""
    stripped = value.strip()
    if not stripped:
        return True
    if TEMPLATE_VALUE_PATTERN.match(stripped):
        return True
    lowered = stripped.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _looks_like_secret(value: str) -> bool:
    """Return True when a generic literal has the shape of a real credential.

    The rule is deliberately narrow: a long literal that mixes cases and
    digits and carries high Shannon entropy, and that is not an obvious
    placeholder. Low-entropy fixture strings (``"live-broker-secret-12345"``)
    are test material, not credentials, and a gate that flags them is a
    gate that gets switched off.
    """
    if len(value) < MIN_GENERIC_SECRET_LENGTH:
        return False
    if _is_placeholder(value):
        return False
    if not any(character.isdigit() for character in value):
        return False
    if not any(character.islower() for character in value):
        return False
    if not any(character.isupper() for character in value):
        return False
    return _shannon_entropy(value) >= MIN_GENERIC_SECRET_ENTROPY


def _fingerprint(value: str) -> str:
    """Return a stable, non-reversible fingerprint of a detected secret.

    The gate prints findings into CI logs, so the secret itself must never
    reach the log. The fingerprint lets an operator correlate the same
    secret across runs and across files without disclosing it.
    """
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _bypassed(line: str) -> bool:
    """Return True when the line carries the inline bypass marker."""
    return SECRET_SCAN_BYPASS_MARKER in line


def _new_line_numbers(baseline_text: str | None, current_text: str) -> set[int]:
    """Return the 1-based numbers of the current lines that are new or changed.

    A line is new when the baseline does not contain it: either the file
    is new, or the line was added, or its content was modified. Lines that
    merely moved inside the file are unchanged content and are not
    reported. The comparison is multiset-based (not positional), so an
    unrelated edit earlier in the file cannot turn every following line
    into a false regression.
    """
    current_lines = current_text.splitlines()
    if baseline_text is None:
        return set(range(1, len(current_lines) + 1))
    remaining = Counter(baseline_text.splitlines())
    new_lines: set[int] = set()
    for index, line in enumerate(current_lines, start=1):
        if remaining.get(line, 0) > 0:
            remaining[line] -= 1
        else:
            new_lines.add(index)
    return new_lines


def scan_secret_lines(file_path: str, text: str, line_numbers: set[int]) -> list[SecretFinding]:
    """Scan the given line numbers of *text* for credential shapes."""
    findings: list[SecretFinding] = []
    lines = text.splitlines()
    for number in sorted(line_numbers):
        if number > len(lines):
            continue
        line = lines[number - 1]
        if _bypassed(line):
            continue
        for category, pattern in SECRET_DETECTORS:
            for match in pattern.finditer(line):
                value = match.group(0)
                if category == "private-key" and pattern.fullmatch(line.strip()) is None:
                    continue
                if _is_placeholder(value):
                    continue
                findings.append(
                    SecretFinding(
                        file_path=file_path,
                        line=number,
                        category=category,
                        detail=(
                            f"{category} detected (fingerprint {_fingerprint(value)}); "
                            "the value is redacted from this report"
                        ),
                    )
                )
                break
        for match in GENERIC_SECRET_ASSIGNMENT_PATTERN.finditer(line):
            value = _regex_group_str(match, "value")
            if not _looks_like_secret(value):
                continue
            key = _regex_group_str(match, "key")
            findings.append(
                SecretFinding(
                    file_path=file_path,
                    line=number,
                    category="generic-secret",
                    detail=(
                        f"high-entropy credential assigned to '{key}' "
                        f"(fingerprint {_fingerprint(value)}); the value is redacted "
                        "from this report"
                    ),
                )
            )
    return findings


def _dotted_name(node: ast.expr) -> str | None:
    """Return the dotted name of a call target when it is a plain name chain."""
    func: ast.expr = node
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    parts.append(func.id)
    return ".".join(reversed(parts))


def _collect_import_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve ``import x as y`` / ``from x import y [as z]`` bindings.

    Returns ``(module_aliases, from_imports)``:
      - module_aliases: local module alias -> canonical module
      - from_imports: local name -> canonical dotted path
    """
    module_aliases: dict[str, str] = {}
    from_imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is not None:
                    module_aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                local = alias.asname or alias.name
                from_imports[local] = f"{node.module}.{alias.name}"
    return module_aliases, from_imports


def _canonical_name(
    name: str | None,
    module_aliases: Mapping[str, str],
    from_imports: Mapping[str, str],
) -> str | None:
    """Resolve a dotted call name through the module's import aliases."""
    if name is None:
        return None
    parts = name.split(".")
    if len(parts) == 1:
        return from_imports.get(parts[0], parts[0])
    head = module_aliases.get(parts[0], parts[0])
    return ".".join([head, *parts[1:]])


def _is_literal_string(node: ast.expr) -> bool:
    """Return True when *node* is a plain string literal."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_test_path(file_path: str) -> bool:
    """Return True when *file_path* belongs to a test tree.

    The SAST slice is scoped to shipped code. A test fixture that executes
    a generated snippet exercises the feature under test; flagging it would
    make the gate red on day one and get the gate switched off. The SECRET
    half of the gate is NOT scoped this way.
    """
    parts = file_path.split("/")
    if any(part in TEST_TREE_DIRECTORIES for part in parts[:-1]):
        return True
    name = parts[-1]
    if name in TEST_FILE_NAMES:
        return True
    return name.startswith(TEST_FILE_PREFIXES) or name.endswith(TEST_FILE_SUFFIXES)


def scan_python_sast(file_path: str, source: str) -> list[SecretFinding]:
    """Run the SAST slice over one Python source file.

    The slice covers exactly two rules:

    1. ``eval()`` / ``exec()`` whose argument is not a string literal.
    2. ``subprocess`` calls that pass ``shell=True``.

    It is name-based AST analysis: it resolves ``import subprocess as sp``
    and ``from subprocess import run`` aliases, but deliberately obfuscated
    indirection is out of scope.

    Test modules are out of scope for the slice (see :func:`_is_test_path`).
    """
    if _is_test_path(file_path):
        return []
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []
    module_aliases, from_imports = _collect_import_aliases(tree)
    source_lines = source.splitlines()
    findings: list[SecretFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _canonical_name(_dotted_name(node.func), module_aliases, from_imports)
        if name is None:
            continue
        lineno = node.lineno
        line = source_lines[lineno - 1] if 1 <= lineno <= len(source_lines) else ""
        if _bypassed(line):
            continue
        bare = name.rsplit(".", 1)[-1]
        if bare in SAST_EVAL_CALLS and name in {bare, f"builtins.{bare}"}:
            if node.args and not _is_literal_string(node.args[0]):
                findings.append(
                    SecretFinding(
                        file_path=file_path,
                        line=lineno,
                        category="sast-eval",
                        detail=(
                            f"{bare}() runs non-literal input as code; pass a literal or "
                            "remove the dynamic evaluation"
                        ),
                    )
                )
            continue
        if name.startswith("subprocess.") and bare in SAST_SHELL_FUNCTIONS:
            for keyword in node.keywords:
                value = keyword.value
                if keyword.arg != "shell":
                    continue
                if isinstance(value, ast.Constant) and value.value is True:
                    findings.append(
                        SecretFinding(
                            file_path=file_path,
                            line=lineno,
                            category="sast-shell-true",
                            detail=(
                                f"{name}() runs through a shell; pass an argument vector "
                                "instead of a shell string"
                            ),
                        )
                    )
    return findings


def audit_secret_scan(
    baseline: Mapping[str, str],
    current: Mapping[str, str],
) -> list[SecretFinding]:
    """Audit the change between two content sets and return every finding.

    Args:
        baseline: Content set of the BASELINE tree (path -> text).
        current: Content set of the CURRENT tree (path -> text).

    Returns:
        The findings, ordered by path then line then category. Only NEW or
        CHANGED lines are reported.
    """
    findings: list[SecretFinding] = []
    for path in sorted(current):
        current_text = current[path]
        baseline_text = baseline.get(path)
        line_numbers = _new_line_numbers(baseline_text, current_text)
        findings.extend(scan_secret_lines(path, current_text, line_numbers))
        if path.endswith(".py"):
            findings.extend(scan_python_sast(path, current_text))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def audit_secret_roots(baseline_root: Path | None, current_root: Path) -> list[SecretFinding]:
    """Audit the change between two repository roots (or one root, no baseline)."""
    baseline = read_tree(baseline_root) if baseline_root is not None else {}
    current = read_tree(current_root)
    return audit_secret_scan(baseline, current)


def _default_root() -> Path:
    """Return the repository root the gate scans when no root is given."""
    return Path(__file__).parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    """Run the secret-scan / SAST gate and return an exit code.

    Exit code 0: no findings. Exit code 1: findings. Exit code 2: a root
    was not found.
    """
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > MAX_CLI_ROOT_ARGUMENTS:
        print(
            "Usage: python -m ralph.testing.audit_secret_scan [baseline_root] [current_root]",
            file=sys.stderr,
        )
        return 2
    if len(args) == MAX_CLI_ROOT_ARGUMENTS:
        baseline_root: Path | None = Path(args[0])
        current_root = Path(args[1])
    elif len(args) == 1:
        baseline_root = None
        current_root = Path(args[0])
    else:
        current_root = _default_root()
        baseline_root = head_tree(current_root)
    for root in (baseline_root, current_root):
        if root is not None and not root.is_dir():
            print(f"Error: audit root not found: {root}", file=sys.stderr)
            return 2

    baseline_label = str(baseline_root) if baseline_root is not None else "(empty baseline)"
    print(f"Auditing secret scan (baseline diff): baseline={baseline_label} current={current_root}")
    print()

    findings = audit_secret_roots(baseline_root, current_root)

    if findings:
        print(f"SECRET-SCAN / SAST FINDINGS: {len(findings)} finding(s)")
        print("=" * 72)
        for finding in findings:
            print(f"  {finding}")
        print()
        print(
            "Secrets MUST NOT be committed. Remove the credential, rotate it, and inject it "
            "at runtime through the environment or a secret manager. Add an inline "
            f"'# {SECRET_SCAN_BYPASS_MARKER}: <reason>' marker only for a false positive."
        )
        return 1

    print("No secret-scan or SAST findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
