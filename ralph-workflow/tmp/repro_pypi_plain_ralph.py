from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

WHEEL = Path(
    "/var/folders/8b/nh540lxx24q3kjpjsr32crp00000gn/T/opencode/pypi-ralph-0.8.0/"
    "ralph_workflow-0.8.0-py3-none-any.whl"
)


def run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True, check=False)


root = Path(tempfile.mkdtemp(prefix="ralph-pypi-plain-"))
venv = root / "venv"
project = root / "project"
xdg = root / "xdg"
home = root / "home"
project.mkdir()
xdg.mkdir()
home.mkdir()

create_venv = run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], cwd=root)
print("CREATE_VENV_RC", create_venv.returncode)
print(create_venv.stdout)
print(create_venv.stderr)

py = venv / "bin" / "python"
install = run([str(py), "-m", "pip", "install", "--no-deps", str(WHEEL)], cwd=root)
print("INSTALL_RC", install.returncode)
print(install.stdout)
print(install.stderr)

env = os.environ.copy()
env["XDG_CONFIG_HOME"] = str(xdg)
env["HOME"] = str(home)

version = run([str(py), "-m", "ralph", "--version"], cwd=project, env=env)
print("VERSION_RC", version.returncode)
print(version.stdout)
print(version.stderr)

plain = run([str(py), "-m", "ralph"], cwd=project, env=env)
print("PLAIN_RALPH_RC", plain.returncode)
print("PLAIN_STDOUT_START")
print(plain.stdout)
print("PLAIN_STDOUT_END")
print("PLAIN_STDERR_START")
print(plain.stderr)
print("PLAIN_STDERR_END")

cfg = xdg / "ralph-workflow.toml"
print("CFG_EXISTS", cfg.exists())
if cfg.exists():
    text = cfg.read_text(encoding="utf-8")
    print("CFG_CONTAINS", {
        "planning_analysis": 'planning_analysis = "analysis"' in text,
        "development_analysis": 'development_analysis = "analysis"' in text,
        "development_commit": 'development_commit = "commit"' in text,
    })
    print("CFG_TEXT_START")
    print(text)
    print("CFG_TEXT_END")
