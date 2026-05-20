#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
POSITIONING = WORKSPACE / 'agents' / 'marketing' / 'RALPH_WORKFLOW_POSITIONING.md'
CHECKER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_check.py'
EDITORIAL = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_editorial_audit.py'
AGENTIC = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_agentic_review.py'
STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_latest.md'
EDITORIAL_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_editorial_latest.md'
AGENTIC_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.md'


def run_py(path: Path) -> tuple[int, str]:
    proc = subprocess.run(['python3', str(path)], capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def combined_check() -> tuple[int, str]:
    c_code, c_out = run_py(CHECKER)
    e_code, e_out = run_py(EDITORIAL)
    a_code, a_out = run_py(AGENTIC)
    code = 0 if c_code == 0 and e_code == 0 and a_code == 0 else 1
    out = f'CHECKER\n{c_out}\n\nEDITORIAL\n{e_out}\n\nAGENTIC\n{a_out}'
    return code, out


def conservative_repairs() -> list[str]:
    repairs: list[str] = []
    repairs.append('No conservative deterministic docs repair was attempted.')
    return repairs


def write_status(before: str, after: str, repairs: list[str]) -> None:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    repair_lines = '\n'.join(f'- {r}' for r in repairs)
    STATUS.write_text(
        f'''# Ralph Docs Watchdog Status\n\nStatus: remediation attempt complete pending independent verification\n\nTimestamp:\n- {now}\n\nCanonical positioning source:\n- `{POSITIONING}`\n\nChecker command:\n- `python3 {CHECKER}`\n\nEditorial audit command:\n- `python3 {EDITORIAL}`\n\nAgentic review command:\n- `python3 {AGENTIC}`\n\n## Before results\n```\n{before}\n```\n\n## Deterministic repairs made this run\n{repair_lines}\n\n## After results\n```\n{after}\n```\n\n## Editorial report artifact\n- `{EDITORIAL_REPORT}`\n\n## Agentic review artifact\n- `{AGENTIC_REPORT}`\n\n## Verification state\n- This watchdog must fail hard when top-level docs drift from canonical positioning.\n- It must not self-certify success after typo-only or template-driven changes.\n- Agentic review is the primary quality judge; deterministic checks are secondary tripwires.\n- Independent verification is still required.\n''',
        encoding='utf-8',
    )


def main() -> int:
    before_code, before_out = combined_check()
    repairs = conservative_repairs()
    after_code, after_out = combined_check()
    write_status(before_out, after_out, repairs)
    print('BEFORE')
    print(before_out)
    print('AFTER')
    print(after_out)
    if repairs:
        print('REPAIRS')
        for repair in repairs:
            print('-', repair)
    return after_code if before_code == 0 else before_code or after_code


if __name__ == '__main__':
    raise SystemExit(main())
