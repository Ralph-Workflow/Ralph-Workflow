"""Tests for the Node upstream skill sync script."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_skills_package_prepack_uses_sync_script() -> None:
    package_json = Path(__file__).parent.parent / "skills-package" / "package.json"
    data = json.loads(package_json.read_text(encoding="utf-8"))
    assert data["scripts"]["prepack"] == "npm run sync:upstream-skills"
    assert "cpSync" not in data["scripts"]["prepack"]


def test_upstream_manifest_covers_all_shipped_skills_and_has_no_local_sources() -> None:
    repo_root = Path(__file__).parent.parent
    manifest_path = repo_root / "skills-package" / "upstream-skills.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_names = tuple(skill["name"] for skill in manifest["skills"])

    from ralph.skills._content import BASELINE_SKILL_NAMES

    assert bundle_names == BASELINE_SKILL_NAMES
    assert all(skill["source"]["type"] == "upstream" for skill in manifest["skills"])


def test_sync_script_fetches_upstream_content(tmp_path: Path) -> None:
    node_binary = which("node")
    assert node_binary is not None

    upstream_root = tmp_path / "upstream"
    skill_path = upstream_root / "example" / "upstream" / "main" / "skills" / "demo-skill"
    skill_path.mkdir(parents=True)
    skill_content = "---\nname: demo-skill\n---\n\n# Demo Skill\n"
    (skill_path / "SKILL.md").write_text(skill_content, encoding="utf-8")

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source_repo": "https://github.com/example/upstream",
                "source_ref": "main",
                "source_version": "v0",
                "source_repos": ["https://github.com/example/upstream"],
                "skills": [
                    {
                        "name": "demo-skill",
                        "source": {
                            "type": "upstream",
                            "repo": "https://github.com/example/upstream",
                            "ref": "main",
                            "path": "skills/demo-skill/SKILL.md",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    port = _free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(upstream_root))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        script = Path(__file__).parent.parent / "skills-package" / "bin" / "sync-upstream-skills.js"
        env = {
            **os.environ,
            "RALPH_SKILLS_MANIFEST": str(manifest_path),
            "RALPH_SKILLS_OUTPUT_DIRS": str(output_dir),
            "RALPH_SKILLS_UPSTREAM_RAW_BASE": f"http://127.0.0.1:{port}",
            "RALPH_SKILLS_SOURCE_COMMIT": "fixture-commit",
            "RALPH_SKILLS_MIRRORED_AT": "2026-05-26T00:00:00Z",
        }
        subprocess.run([node_binary, str(script)], check=True, env=env)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    synced_content = (output_dir / "demo-skill.md").read_text(encoding="utf-8")
    assert synced_content.startswith("---\nname: demo-skill")
    metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["source_commit"] == "fixture-commit"
    assert metadata["skills"] == ["demo-skill"]
    assert metadata["source_repos"] == ["https://github.com/example/upstream"]
    assert metadata["skill_sources"]["demo-skill"]["repo"] == "https://github.com/example/upstream"
