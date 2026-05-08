from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

root = Path('/tmp/ralph-podman-home')
home = root / 'home'
xdg = home / '.config'
project = root / 'project'
home.mkdir(parents=True, exist_ok=True)
xdg.mkdir(parents=True, exist_ok=True)
project.mkdir(parents=True, exist_ok=True)

env = os.environ.copy()
env['HOME'] = str(home)
env['XDG_CONFIG_HOME'] = str(xdg)

def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, check=False)

apt = run(['apt-get', 'update'])
print('APT_UPDATE_RC', apt.returncode)
print(apt.stdout)
print(apt.stderr)

git_install = run(['apt-get', 'install', '-y', 'git'])
print('APT_GIT_RC', git_install.returncode)
print(git_install.stdout)
print(git_install.stderr)

install = run([sys.executable, '-m', 'pip', 'install', 'ralph-workflow==0.8.0'])
print('INSTALL_RC', install.returncode)
print(install.stdout)
print(install.stderr)

version = run(['ralph', '--version'])
print('VERSION_RC', version.returncode)
print(version.stdout)
print(version.stderr)

plain = run(['ralph'], cwd=project)
print('PLAIN_RC', plain.returncode)
print('PLAIN_STDOUT_START')
print(plain.stdout)
print('PLAIN_STDOUT_END')
print('PLAIN_STDERR_START')
print(plain.stderr)
print('PLAIN_STDERR_END')

cfg = xdg / 'ralph-workflow.toml'
print('CFG_EXISTS', cfg.exists())
if cfg.exists():
    text = cfg.read_text(encoding='utf-8')
    print('CFG_CONTAINS', {
        'planning_analysis': 'planning_analysis = "analysis"' in text,
        'development_analysis': 'development_analysis = "analysis"' in text,
        'development_commit': 'development_commit = "commit"' in text,
    })
    print('CFG_TEXT_START')
    print(text)
    print('CFG_TEXT_END')
