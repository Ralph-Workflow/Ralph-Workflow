#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path('/home/mistlight/.openclaw/workspace')
CHECKER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_check.py'
EDITORIAL = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_editorial_audit.py'
AGENTIC = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_agentic_review.py'
RUNNER = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_docs_runner.py'
EDITORIAL_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_editorial_latest.md'
AGENTIC_REPORT = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_agentic_latest.md'
RUNNER_STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_latest.md'
VERIFIER_STATUS = WORKSPACE / 'agents' / 'docs_quality' / 'ralph_verifier_latest.md'


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


def write_status(status: str, pre: str, post: str, remediation_forced: bool) -> None:
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    VERIFIER_STATUS.write_text(
        f'''# Ralph Docs Independent Verifier Status\n\nStatus: {status}\n\nTimestamp:\n- {now}\n\n## Pre-verification results\n```\n{pre}\n```\n\n## Remediation forced by verifier\n- {'yes' if remediation_forced else 'no'}\n\n## Final verification results\n```\n{post}\n```\n\n## Evidence artifacts\n- runner status: `{RUNNER_STATUS}`\n- editorial audit: `{EDITORIAL_REPORT}`\n- agentic review: `{AGENTIC_REPORT}`\n- verifier status: `{VERIFIER_STATUS}`\n''',
        encoding='utf-8',
    )


def main() -> int:
    pre_code, pre_out = combined_check()
    remediation_forced = False
    final_code, final_out = pre_code, pre_out

    if pre_code != 0:
        remediation_forced = True
        run_py(RUNNER)
        final_code, final_out = combined_check()

    status = 'independently verified pass' if final_code == 0 else 'independent verifier failed signoff'
    write_status(status, pre_out, final_out, remediation_forced)
    print(status)
    print(final_out)
    return final_code


if __name__ == '__main__':
    raise SystemExit(main())
