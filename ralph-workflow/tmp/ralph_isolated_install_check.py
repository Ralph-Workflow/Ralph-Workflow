from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path('/Users/mistlight/Projects/RalphWithReviewer/ralph-workflow')
SDIST = REPO / 'dist' / 'ralph_workflow-0.8.0.tar.gz'
WHEEL = REPO / 'dist' / 'ralph_workflow-0.8.0-py3-none-any.whl'


def run(cmd: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, cwd=cwd, text=True, capture_output=True, check=False)


def scenario(artifact: Path) -> dict[str, object]:
    root = Path(tempfile.mkdtemp(prefix='ralph-isolated-'))
    venv = root / 'venv'
    project = root / 'project'
    xdg = root / 'xdg'
    home = root / 'home'
    project.mkdir()
    xdg.mkdir()
    home.mkdir()

    create = run([sys.executable, '-m', 'venv', '--system-site-packages', str(venv)])
    if create.returncode != 0:
        return {'artifact': artifact.name, 'stage': 'venv', 'returncode': create.returncode, 'stdout': create.stdout, 'stderr': create.stderr}

    py = venv / 'bin' / 'python'
    install = run([str(py), '-m', 'pip', 'install', '--no-deps', str(artifact)])
    if install.returncode != 0:
        return {'artifact': artifact.name, 'stage': 'install', 'returncode': install.returncode, 'stdout': install.stdout, 'stderr': install.stderr}

    env = os.environ.copy()
    env['XDG_CONFIG_HOME'] = str(xdg)
    env['HOME'] = str(home)

    version = run([str(py), '-m', 'ralph', '--version'], env=env, cwd=project)
    check = run([str(py), '-m', 'ralph', '--check-config'], env=env, cwd=project)
    load = run(
        [
            str(py),
            '-c',
            'from pathlib import Path; from ralph.config.loader import load_config; from ralph.policy.loader import load_policy; from ralph.workspace.scope import WorkspaceScope; scope=WorkspaceScope(Path.cwd()); cfg=load_config(workspace_scope=scope); bundle=load_policy(Path.cwd()/".agent", config=cfg); print(sorted(bundle.agents.agent_drains))',
        ],
        env=env,
        cwd=project,
    )
    global_cfg = xdg / 'ralph-workflow.toml'
    cfg_text = global_cfg.read_text(encoding='utf-8') if global_cfg.exists() else '<missing>'
    return {
        'artifact': artifact.name,
        'stage': 'done',
        'version_rc': version.returncode,
        'version_out': version.stdout,
        'version_err': version.stderr,
        'check_rc': check.returncode,
        'check_out': check.stdout,
        'check_err': check.stderr,
        'load_rc': load.returncode,
        'load_out': load.stdout,
        'load_err': load.stderr,
        'global_cfg': cfg_text,
    }


for artifact in (SDIST, WHEEL):
    result = scenario(artifact)
    print('=' * 80)
    print(result['artifact'])
    for key, value in result.items():
        if key == 'artifact':
            continue
        print(f'--- {key} ---')
        print(value)
