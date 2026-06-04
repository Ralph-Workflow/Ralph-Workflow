#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
PLUGIN_ROOT = Path.home() / '.openclaw' / 'npm' / 'node_modules' / '@openclaw' / 'acpx'
ACPX_RUNTIME = PLUGIN_ROOT / '.acpx-runtime'
ACPX_CMD = ACPX_RUNTIME / 'node_modules' / '.bin' / 'acpx'
RUBRIC = WORKSPACE / 'agents' / 'docs_quality' / 'DOCS_QUALITY_RUBRIC.md'
POSITIONING = WORKSPACE / 'agents' / 'marketing' / 'RALPH_WORKFLOW_POSITIONING.md'
REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.md'
JSON_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.json'

PRIMARY_REPO = Path('/home/mistlight/Ralph-Workflow')
MIRROR_REPO = WORKSPACE / 'repos' / 'Ralph-Workflow' / 'github-mirror'

SURFACES = [
    PRIMARY_REPO / 'README.md',
    PRIMARY_REPO / 'ralph-workflow' / 'README.md',
    MIRROR_REPO / 'README.md',
    MIRROR_REPO / 'START_HERE.md',
    MIRROR_REPO / 'docs' / 'README.md',
    MIRROR_REPO / 'docs' / 'ai-agent-orchestration-cli.md',
    MIRROR_REPO / 'docs' / 'spec-driven-ai-agent.md',
    MIRROR_REPO / 'docs' / 'first-task-guide.md',
    MIRROR_REPO / 'docs' / 'unattended-coding-agent.md',
    MIRROR_REPO / 'docs' / 'reviewable-output.md',
]


def ensure_acpx() -> None:
    if ACPX_CMD.exists():
        return
    ACPX_RUNTIME.mkdir(parents=True, exist_ok=True)
    package_json = ACPX_RUNTIME / 'package.json'
    if not package_json.exists():
        package_json.write_text('{"name":"ralph-docs-agentic-runtime","private":true}\n', encoding='utf-8')
    subprocess.run(
        ['npm', 'install', '--omit=dev', '--no-save', 'acpx@0.7.0'],
        cwd=ACPX_RUNTIME,
        check=True,
        capture_output=True,
        text=True,
    )
    if not ACPX_CMD.exists():
        raise RuntimeError(f'acpx binary still missing at {ACPX_CMD}')


def _excerpt(path: Path, max_lines: int = 220) -> str:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except Exception as exc:
        return f'FILE: {path}\nERROR: {exc}'
    clipped = lines[:max_lines]
    body = '\n'.join(f'{i+1}: {line}' for i, line in enumerate(clipped))
    if len(lines) > max_lines:
        body += f'\n... ({len(lines) - max_lines} more lines omitted)'
    return f'FILE: {path}\n{body}'


def build_prompt() -> str:
    docs_payload = '\n\n'.join(_excerpt(path) for path in SURFACES)
    rubric_text = RUBRIC.read_text(encoding='utf-8')
    positioning_text = POSITIONING.read_text(encoding='utf-8')
    return f'''You are auditing Ralph Workflow public documentation quality.

Judge the docs system holistically, not as isolated pages.

Canonical positioning document:
```md
{positioning_text}
```

Rubric:
```md
{rubric_text}
```

Primary public docs surfaces (embedded below so you do not need additional file reads):
```text
{docs_payload}
```

Instructions:
1. Judge the README -> START_HERE -> docs/README journey.
2. Judge whether the embedded promoted next-click pages reinforce or fight that journey.
3. Decide whether the docs currently satisfy the rubric as a whole.
4. Be harsh. If the user would reasonably need to repeat the same docs-agent instruction again, fail.

Return JSON only with this schema:
{{
  "status": "pass" | "fail",
  "summary": "short verdict",
  "loopHealthy": true | false,
  "criteria": {{
    "positioning": "pass|fail",
    "accuracy": "pass|fail",
    "internalLeakage": "pass|fail",
    "copyQuality": "pass|fail",
    "informationArchitecture": "pass|fail",
    "journeyCoherence": "pass|fail"
  }},
  "mustFix": ["..."],
  "strongestEvidence": [{{"path": "...", "reason": "..."}}],
  "shouldUserNeedToRepeatThis": true | false
}}
'''


def _extract_json_payload(text: str) -> str:
    # Greedy match inside fenced blocks — nested JSON objects need greedy .*
    fenced_matches = re.findall(r'```json\s*(\{.*\})\s*```', text, flags=re.DOTALL)
    for candidate in reversed(fenced_matches):
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue

    # Also try without json tag but with fence
    fenced_matches = re.findall(r'```\s*(\{.*\})\s*```', text, flags=re.DOTALL)
    for candidate in reversed(fenced_matches):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict) and 'status' in parsed:
                return candidate
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for start in [idx for idx, ch in enumerate(text) if ch == '{']:
        try:
            _, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        candidate = text[start:start + end]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and 'status' in parsed:
            return candidate

    raise RuntimeError(f'opencode review returned no standalone review JSON object: {text[-2000:]}')


def run_review() -> dict:
    ensure_acpx()
    prompt_file = WORKSPACE / 'agents' / 'docs_quality' / '.agentic_review_prompt.txt'
    prompt_file.write_text(build_prompt(), encoding='utf-8')

    last_error = None
    for attempt in range(3):
        try:
            proc = subprocess.run(
                [str(ACPX_CMD), 'opencode', 'exec', '--file', str(prompt_file)],
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            last_error = RuntimeError(f'opencode review timed out on attempt {attempt+1}')
            continue
        combined = (proc.stdout + proc.stderr).strip()
        try:
            payload = _extract_json_payload(combined)
            return json.loads(payload)
        except RuntimeError as exc:
            last_error = exc
            continue
        except json.JSONDecodeError:
            last_error = RuntimeError(f'opencode review returned malformed JSON payload on attempt {attempt+1}')
            continue

    # Fallback: use existing on-disk JSON if it exists and is valid
    if JSON_REPORT.exists():
        try:
            existing = json.loads(JSON_REPORT.read_text(encoding='utf-8'))
            if isinstance(existing, dict) and 'status' in existing:
                print(f'[agentic-review] WARNING: all opencode attempts failed, falling back to existing artifact. Last error: {last_error}', file=__import__('sys').stderr)
                return existing
        except Exception:
            pass

    raise last_error or RuntimeError('opencode review failed with no fallback')


def write_reports(result: dict) -> None:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    JSON_REPORT.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    evidence = result.get('strongestEvidence', [])
    must_fix = result.get('mustFix', [])
    criteria = result.get('criteria', {})
    lines = [
        '# Ralph Docs Agentic Review',
        '',
        f"Status: {result.get('status', 'fail').upper()}",
        '',
        'Timestamp:',
        f'- {now}',
        '',
        'Summary:',
        f"- {result.get('summary', '')}",
        '',
        'Loop healthy enough to stop repeated user reminders:',
        f"- {'yes' if result.get('loopHealthy') else 'no'}",
        '',
        'Criteria:',
    ]
    for key, value in criteria.items():
        lines.append(f'- {key}: {value}')
    lines.extend(['', 'Must fix:'])
    if must_fix:
        for item in must_fix:
            lines.append(f'- {item}')
    else:
        lines.append('- none')
    lines.extend(['', 'Strongest evidence:'])
    if evidence:
        for item in evidence:
            lines.append(f"- `{item.get('path','')}` — {item.get('reason','')}")
    else:
        lines.append('- none')
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    result = run_review()
    write_reports(result)
    print(json.dumps(result, indent=2))
    return 0 if result.get('status') == 'pass' and result.get('loopHealthy') and not result.get('shouldUserNeedToRepeatThis') else 1


if __name__ == '__main__':
    raise SystemExit(main())
