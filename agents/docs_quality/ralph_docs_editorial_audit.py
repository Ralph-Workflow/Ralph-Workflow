#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from collections import Counter

WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
PRIMARY_REPO = Path('/home/mistlight/RalphWithReviewer')
MIRROR_REPO = WORKSPACE / 'repos' / 'Ralph-Workflow' / 'github-mirror'
POSITIONING = WORKSPACE / 'agents' / 'marketing' / 'RALPH_WORKFLOW_POSITIONING.md'
REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_editorial_latest.md'

PRIMARY_ROOT = PRIMARY_REPO / 'README.md'
PRIMARY_CRATE = PRIMARY_REPO / 'ralph-workflow' / 'README.md'
MIRROR_ROOT = MIRROR_REPO / 'README.md'
START_HERE = MIRROR_REPO / 'START_HERE.md'
DOCS_INDEX = MIRROR_REPO / 'docs' / 'README.md'
AI_ORCH = MIRROR_REPO / 'docs' / 'ai-agent-orchestration-cli.md'
SPEC_DRIVEN = MIRROR_REPO / 'docs' / 'spec-driven-ai-agent.md'
FIRST_TASK = MIRROR_REPO / 'docs' / 'first-task-guide.md'
UNATTENDED = MIRROR_REPO / 'docs' / 'unattended-coding-agent.md'
REVIEWABLE = MIRROR_REPO / 'docs' / 'reviewable-output.md'
SPHINX_INDEX = MIRROR_REPO / 'ralph-workflow' / 'docs' / 'sphinx' / 'index.rst'
SPHINX_GETTING_STARTED = MIRROR_REPO / 'ralph-workflow' / 'docs' / 'sphinx' / 'getting-started.md'
SPHINX_AI_ORCH = MIRROR_REPO / 'ralph-workflow' / 'docs' / 'sphinx' / 'ai-agent-orchestration-cli.md'

TOP_LEVEL_SURFACES = [
    PRIMARY_ROOT,
    PRIMARY_CRATE,
    MIRROR_ROOT,
    START_HERE,
    DOCS_INDEX,
    AI_ORCH,
    SPEC_DRIVEN,
    FIRST_TASK,
    UNATTENDED,
    REVIEWABLE,
    SPHINX_INDEX,
    SPHINX_GETTING_STARTED,
    SPHINX_AI_ORCH,
]

PRODUCT_SURFACES = [
    PRIMARY_ROOT,
    PRIMARY_CRATE,
    MIRROR_ROOT,
    START_HERE,
    AI_ORCH,
    SPEC_DRIVEN,
    FIRST_TASK,
    UNATTENDED,
    SPHINX_INDEX,
    SPHINX_GETTING_STARTED,
    SPHINX_AI_ORCH,
]

PROOF_SURFACES = [
    REVIEWABLE,
]

PUBLIC_DOC_ROOTS = [
    MIRROR_REPO / 'docs',
    MIRROR_REPO / 'ralph-workflow' / 'docs' / 'sphinx',
]

PUBLIC_DOC_EXCLUDE_PARTS = {
    'mcp',
    '_build',
    '_static',
    '_themes',
    '__pycache__',
}

PUBLIC_DOC_EXCLUDE_FILES = {
    'artifacts.md',
    'developer-internals.md',
    'developer-reference.md',
    'modules.rst',
    'configuration.md',
    'cli.md',
    'concepts.md',
    'prompts.md',
    'recovery.md',
    'parallel-mode.md',
    'policy-driven-overhaul-migration.md',
    'versioning.md',
    'agents.md',
    'troubleshooting.md',
}

FIRST_SCREEN_BANNED = [
    'reviewable result',
    'not just a transcript',
    'merge decision',
    'would i merge this',
    'judge the handoff',
    'bounded diff',
    'handoff standard',
    'finish receipt',
]

TOP_LEVEL_INTERNALS_BANNED = [
    'artifacts',
    'review bundle',
    'agent-to-agent',
    'workflow plumbing',
    'internal handoff',
]


@dataclass
class Issue:
    path: Path
    line: int
    kind: str
    message: str


def text(path: Path) -> str:
    return path.read_text(encoding='utf-8')


def first_screen(path: Path, lines: int = 60) -> str:
    return '\n'.join(text(path).splitlines()[:lines])


def line_of(path: Path, needle: str) -> int:
    for i, line in enumerate(text(path).splitlines(), 1):
        if needle.lower() in line.lower():
            return i
    return 1


def contains_any(haystack: str, needles: Iterable[str]) -> bool:
    low = haystack.lower()
    return any(needle.lower() in low for needle in needles)


def require_any(path: Path, screen: str, needles: list[str], kind: str, message: str) -> list[Issue]:
    if contains_any(screen, needles):
        return []
    return [Issue(path, 1, kind, message)]


def forbid_any(path: Path, screen: str, needles: list[str], kind: str, message_prefix: str) -> list[Issue]:
    issues = []
    low = screen.lower()
    for needle in needles:
        if needle.lower() in low:
            issues.append(Issue(path, line_of(path, needle), kind, f'{message_prefix}: {needle}'))
    return issues


def count_top_links(path: Path, upto_line: int = 120) -> int:
    import re
    content = '\n'.join(text(path).splitlines()[:upto_line])
    content = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', content)
    return content.count('](')


def heading_count(path: Path) -> int:
    count = 0
    in_fence = False
    for line in text(path).splitlines():
        if line.strip().startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith('## '):
            count += 1
    return count


def line_budget(path: Path, max_lines: int) -> list[Issue]:
    total = len(text(path).splitlines())
    if total > max_lines:
        return [Issue(path, 1, 'length-sprawl', f'Too many lines for a top-level surface ({total}). Max {max_lines}.')]
    return []


def public_doc_candidates() -> list[Path]:
    paths: list[Path] = []
    for root in PUBLIC_DOC_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {'.md', '.rst'}:
                continue
            if any(part in PUBLIC_DOC_EXCLUDE_PARTS for part in path.parts):
                continue
            if path.name in PUBLIC_DOC_EXCLUDE_FILES:
                continue
            paths.append(path)
    return sorted(set(paths))


def comprehensive_public_doc_sweep() -> list[Issue]:
    issues: list[Issue] = []
    for path in public_doc_candidates():
        screen = first_screen(path)
        issues.extend(forbid_any(path, screen, FIRST_SCREEN_BANNED, 'public-doc-drift', 'Public doc first screen is using deprecated proof framing'))
        # Internal-detail ban here is narrower than top-level surfaces: it only catches obvious artifact-led intros.
        issues.extend(forbid_any(path, screen, ['reviewable result', 'not just a transcript'], 'public-doc-drift', 'Public doc first screen is still using the old differentiator framing'))
    return issues


def heading_budget(path: Path, max_headings: int) -> list[Issue]:
    total = heading_count(path)
    if total > max_headings:
        return [Issue(path, 1, 'heading-sprawl', f'Too many second-level headings ({total}). Max {max_headings}.')]
    return []


def require_positioning_alignment(path: Path) -> list[Issue]:
    screen = first_screen(path)
    issues: list[Issue] = []
    issues.extend(require_any(path, screen, ['operating system for autonomous coding', 'ai agent orchestration'], 'positioning-gap', 'First screen must explain that Ralph Workflow is an AI agent orchestrator.'))
    issues.extend(require_any(path, screen, ['simple ralph-loop', 'simple ralph loop', 'simple at the center', 'core stays simple', 'simple core'], 'positioning-gap', 'First screen must explain the simple Ralph-loop core.'))
    issues.extend(require_any(path, screen, ['composable loop', 'composable workflow', 'powerful in composition', 'complex workflows', 'compose more complex workflows'], 'positioning-gap', 'First screen must explain composition into more complex workflows.'))
    issues.extend(require_any(path, screen, ['strong default workflow', 'default workflow', 'build on top of it', 'use that default', 'use the default as-is'], 'positioning-gap', 'First screen must explain the strong default workflow and extensibility story.'))
    issues.extend(forbid_any(path, screen, FIRST_SCREEN_BANNED, 'positioning-drift', 'First screen is dominated by deprecated proof framing'))
    issues.extend(forbid_any(path, screen, TOP_LEVEL_INTERNALS_BANNED, 'internal-detail-drift', 'Top-level/public docs are leading with internal detail'))
    return issues


def require_proof_page_alignment(path: Path) -> list[Issue]:
    screen = first_screen(path)
    issues: list[Issue] = []
    issues.extend(require_any(path, screen, ['supporting proof', 'not the product pitch', 'what proof should exist', 'what a finished run should prove'], 'proof-page-gap', 'Proof page must clearly subordinate proof details to the main product story.'))
    issues.extend(forbid_any(path, screen, ['merge decision', 'would i merge this', 'bounded diff'], 'proof-page-drift', 'Proof page is drifting back to deprecated merge/diff framing'))
    return issues


def require_task_framing(path: Path) -> list[Issue]:
    screen = first_screen(path)
    issues: list[Issue] = []
    issues.extend(require_any(path, screen, ['too big to babysit', 'ambitious', 'well-specified', 'substantial'], 'fit-gap', 'Surface must frame Ralph Workflow as substantial, well-specified work.'))
    issues.extend(forbid_any(path, screen, ['small enough to judge in one sitting', 'cheap to roll back', 'cheap rollback'], 'fit-drift', 'Surface is drifting toward small-task framing'))
    return issues


def require_docs_map(path: Path) -> list[Issue]:
    screen = first_screen(path)
    issues: list[Issue] = []
    issues.extend(require_any(path, screen, ['documentation map'], 'docs-map-gap', 'Docs index must clearly identify itself as a map.'))
    issues.extend(require_any(path, screen, ['ai-agent-orchestration-cli.md', 'ai agent orchestration'], 'docs-map-gap', 'Docs index must route users through product framing, not just proof pages.'))
    issues.extend(forbid_any(path, screen, ['reviewable-output.md', 'review-ai-coding-output-before-merge.md'], 'docs-map-drift', 'Docs index is over-weighting proof/detail pages on the first screen'))
    return issues


def require_repo_hierarchy(path: Path) -> list[Issue]:
    screen = first_screen(path)
    issues: list[Issue] = []
    if 'github is the mirror' in screen.lower() or 'github mirror' in screen.lower():
        issues.extend(require_any(path, screen, ['codeberg is the primary repo', 'codeberg is the primary', 'codeberg primary'], 'repo-hierarchy-gap', 'Surface mentions GitHub without clearly anchoring Codeberg as primary.'))
    return issues


def audit() -> list[Issue]:
    issues: list[Issue] = []

    if not POSITIONING.exists():
        issues.append(Issue(POSITIONING, 1, 'missing-positioning-source', 'Canonical positioning file missing.'))
        return issues

    for path in TOP_LEVEL_SURFACES:
        if not path.exists():
            issues.append(Issue(path, 1, 'missing-surface', 'Required docs surface is missing.'))
            continue

    for path in PRODUCT_SURFACES:
        issues.extend(require_positioning_alignment(path))
        issues.extend(require_repo_hierarchy(path))

    for path in [PRIMARY_ROOT, PRIMARY_CRATE, MIRROR_ROOT, START_HERE, AI_ORCH, SPEC_DRIVEN, FIRST_TASK, UNATTENDED, SPHINX_GETTING_STARTED, SPHINX_AI_ORCH]:
        issues.extend(require_task_framing(path))

    for path in PROOF_SURFACES:
        issues.extend(require_proof_page_alignment(path))

    issues.extend(require_docs_map(DOCS_INDEX))
    issues.extend(comprehensive_public_doc_sweep())

    budgets = {
        PRIMARY_ROOT: (170, 8, 10),
        PRIMARY_CRATE: (260, 12, 12),
        MIRROR_ROOT: (180, 8, 10),
        START_HERE: (150, 8, 10),
        DOCS_INDEX: (120, 6, 12),
    }
    for path, (max_lines, max_headings, max_links) in budgets.items():
        issues.extend(line_budget(path, max_lines))
        issues.extend(heading_budget(path, max_headings))
        links = count_top_links(path)
        if links > max_links:
            issues.append(Issue(path, 1, 'top-link-budget', f'Too many top-of-page links ({links}). Max {max_links}.'))

    seen = set()
    unique: list[Issue] = []
    for issue in issues:
        key = (issue.path, issue.line, issue.kind, issue.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(issue)
    return unique


def write_report(issues: list[Issue]) -> None:
    by_kind = Counter(issue.kind for issue in issues)
    by_path = Counter(str(issue.path) for issue in issues)
    lines = [
        '# Ralph Docs Editorial Audit',
        '',
        f'Status: {"DOCS_EDITORIAL_OK" if not issues else "DOCS_EDITORIAL_FAIL"}',
        '',
        '## Scope',
        '',
        f'- Canonical positioning: `{POSITIONING}`',
        f'- Product surfaces audited: {len(PRODUCT_SURFACES)}',
        f'- Proof surfaces audited: {len(PROOF_SURFACES)}',
        f'- Public docs swept: {len(public_doc_candidates())}',
        '',
        '## Issue counts by type',
        '',
    ]
    if by_kind:
        for kind, count in sorted(by_kind.items()):
            lines.append(f'- `{kind}`: {count}')
    else:
        lines.append('- none')

    lines.extend(['', '## Most affected files', ''])
    if by_path:
        for path, count in by_path.most_common(20):
            lines.append(f'- `{path}`: {count}')
    else:
        lines.append('- none')

    lines.extend(['', '## Detailed findings', ''])
    if issues:
        for issue in issues:
            lines.append(f'- `{issue.path}:{issue.line}` [{issue.kind}] {issue.message}')
    else:
        lines.append('- none')

    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    issues = audit()
    write_report(issues)
    if not issues:
        print('DOCS_EDITORIAL_OK')
        return 0
    print('DOCS_EDITORIAL_FAIL')
    for issue in issues:
        print(f'- {issue.path}:{issue.line}: [{issue.kind}] {issue.message}')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
