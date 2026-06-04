#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import json

PRIMARY_REPO = Path('/home/mistlight/Ralph-Workflow')
MIRROR_REPO = Path('/home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror')
WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
POSITIONING = WORKSPACE / 'agents' / 'marketing' / 'RALPH_WORKFLOW_POSITIONING.md'
DUPLICATE_POSITIONING = MIRROR_REPO / 'ralph-workflow' / 'docs' / 'plans' / 'marketing-positioning-truths.md'
STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_latest.md'
VERIFIER_STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_verifier_latest.md'
README = WORKSPACE / 'agents' / 'docs_quality' / 'README.md'
CHECKER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_check.py'
EDITORIAL = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_editorial_audit.py'
AGENTIC = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_agentic_review.py'
RUNNER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_runner.py'
VERIFIER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_verify.py'
RUBRIC = WORKSPACE / 'agents' / 'docs_quality' / 'DOCS_QUALITY_RUBRIC.md'
AGENTIC_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.md'
AGENTIC_JSON = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.json'
LOOPS_REGISTRY = WORKSPACE / 'agents' / 'system' / 'self_improvement_loops.json'

REQUIRED_FILES = [
    PRIMARY_REPO / 'README.md',
    PRIMARY_REPO / 'ralph-workflow' / 'README.md',
    MIRROR_REPO / 'README.md',
    MIRROR_REPO / 'START_HERE.md',
    MIRROR_REPO / 'docs' / 'README.md',
    MIRROR_REPO / 'ralph-workflow' / 'README.md',
    POSITIONING,
    README,
    CHECKER,
    EDITORIAL,
    AGENTIC,
    RUNNER,
    VERIFIER,
    RUBRIC,
    LOOPS_REGISTRY,
]

REQUIRED_POSITIONING_PHRASES = [
    'operating system for autonomous coding',
    'simple at the center, powerful in composition',
    'strong default workflow',
    'ai agent orchestrator',
]

REQUIRED_README_PHRASES = [
    'canonical positioning reference',
    'README.md drifts from the canonical positioning doc',
    'agentic review is the primary quality judge',
    'the user would reasonably need to repeat the same docs-agent instruction again',
]

REQUIRED_SCRIPT_REFERENCES = {
    EDITORIAL: ['RALPH_WORKFLOW_POSITIONING.md', 'FIRST_SCREEN_BANNED', 'TOP_LEVEL_SURFACES'],
    AGENTIC: ['DOCS_QUALITY_RUBRIC.md', 'shouldUserNeedToRepeatThis', 'loopHealthy'],
    RUNNER: ['Agentic review command', 'Agentic review artifact', 'agentic review is the primary quality judge'],
    VERIFIER: ['AGENTIC', 'agentic review:', 'independent verifier failed signoff'],
}


@dataclass
class Issue:
    path: Path
    line: int
    kind: str
    message: str


def line_of(path: Path, needle: str) -> int:
    text = path.read_text(encoding='utf-8').splitlines()
    for i, line in enumerate(text, 1):
        if needle.lower() in line.lower():
            return i
    return 1


def inspect_exists(path: Path) -> list[Issue]:
    if path.exists():
        return []
    return [Issue(path, 1, 'missing-file', 'Required docs-quality or docs surface file is missing.')]


def inspect_contains(path: Path, needles: Iterable[str], kind: str) -> list[Issue]:
    if not path.exists():
        return []
    text = path.read_text(encoding='utf-8').lower()
    issues = []
    for needle in needles:
        if needle.lower() not in text:
            issues.append(Issue(path, 1, kind, f'Missing required phrase or reference: {needle}'))
    return issues


def inspect_duplicate_positioning() -> list[Issue]:
    if DUPLICATE_POSITIONING.exists():
        return [Issue(DUPLICATE_POSITIONING, 1, 'duplicate-positioning-doc', 'Duplicate positioning source-of-truth file exists; canonical source must stay singular.')]
    return []


def inspect_loops_registry() -> list[Issue]:
    issues: list[Issue] = []
    if not LOOPS_REGISTRY.exists():
        return issues
    try:
        obj = json.loads(LOOPS_REGISTRY.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return [Issue(LOOPS_REGISTRY, 1, 'invalid-json', 'Self-improvement loop registry is not valid JSON.')]
    loops = obj.get('loops', [])
    target = next((loop for loop in loops if loop.get('name') == 'ralph-docs-watchdog'), None)
    if not target:
        return [Issue(LOOPS_REGISTRY, 1, 'missing-loop-registry-entry', 'ralph-docs-watchdog missing from self-improvement loop registry.')]
    for field in ['runnerScript', 'verifierScript', 'checkerScript', 'editorialAuditScript']:
        if not target.get(field):
            issues.append(Issue(LOOPS_REGISTRY, 1, 'loop-registry-gap', f'Missing loop registry field: {field}'))
    if not target.get('agenticReviewScript'):
        issues.append(Issue(LOOPS_REGISTRY, 1, 'loop-registry-gap', 'Missing loop registry field: agenticReviewScript'))
    if not target.get('agenticReviewArtifact'):
        issues.append(Issue(LOOPS_REGISTRY, 1, 'loop-registry-gap', 'Missing loop registry field: agenticReviewArtifact'))
    return issues


def inspect_agentic_artifacts() -> list[Issue]:
    issues: list[Issue] = []
    for path in [AGENTIC_REPORT, AGENTIC_JSON]:
        if not path.exists():
            issues.append(Issue(path, 1, 'missing-agentic-artifact', 'Agentic review artifact missing.'))
    if issues:
        return issues
    try:
        data = json.loads(AGENTIC_JSON.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return [Issue(AGENTIC_JSON, 1, 'invalid-agentic-json', 'Agentic review JSON artifact is invalid.')] 
    for field in ['status', 'summary', 'loopHealthy', 'criteria', 'mustFix', 'strongestEvidence', 'shouldUserNeedToRepeatThis']:
        if field not in data:
            issues.append(Issue(AGENTIC_JSON, 1, 'agentic-json-gap', f'Agentic review JSON missing field: {field}'))
    return issues


def main() -> int:
    issues: list[Issue] = []
    for path in REQUIRED_FILES:
        issues.extend(inspect_exists(path))

    issues.extend(inspect_contains(POSITIONING, REQUIRED_POSITIONING_PHRASES, 'positioning-source-drift'))
    issues.extend(inspect_contains(README, REQUIRED_README_PHRASES, 'watchdog-readme-drift'))

    for path, needles in REQUIRED_SCRIPT_REFERENCES.items():
        issues.extend(inspect_contains(path, needles, 'watchdog-script-drift'))

    issues.extend(inspect_duplicate_positioning())
    issues.extend(inspect_loops_registry())
    issues.extend(inspect_agentic_artifacts())

    if not issues:
        print('DOCS_QUALITY_OK')
        return 0

    print('DOCS_QUALITY_FAIL')
    for issue in issues:
        print(f'- {issue.path}:{issue.line}: [{issue.kind}] {issue.message}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
