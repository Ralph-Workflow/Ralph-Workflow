"""Bounded-transport tests for the universal upstream skill updater.

The production HTTP client is exercised against a REAL local ``http.server``
bound to 127.0.0.1 on an ephemeral port: no test ever leaves the loopback
interface. Every answer the server receives is checked against the
loopback guard, so a test that points the updater at a remote origin fails
loudly instead of reaching the network.

The suite pins the three transport guarantees of the updater: a redirect is
never followed, the byte cap is enforced WHILE the body streams, and the
timeout covers a slow body. It also pins the inter-process cadence: two
racing PROCESSES that share one cache make exactly ONE upstream attempt.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import pytest

from ralph.skills import upstream
from ralph.skills.upstream import HttpClient, HttpResponse, default_http_client

#: The file drives a REAL socket and spawns REAL interpreters, so it carries
#: the profile marker for real-subprocess tests. The registry entry in
#: ``ralph.test_suites`` keeps every item of this file on the default
#: ``make test`` lane.
pytestmark = [pytest.mark.subprocess_e2e]

if TYPE_CHECKING:
    from ralph.skills.upstream import UpdateOutcome

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

#: The hosts the loopback guard accepts.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

#: How long the server waits for the stall event before it gives up, in
#: seconds. The wait is a SIGNAL wait, not a timed pause: the event the
#: test never sets is what keeps the body mid-transfer.
_STALL_TIMEOUT_SECONDS = 2.0

#: The package root, so a spawned interpreter imports THIS checkout.
_PACKAGE_ROOT = Path(upstream.__file__).resolve().parents[2]

#: The runner a spawned interpreter executes to make one upstream attempt.
_RUNNER = '''"""Make one upstream attempt against the loopback test server."""

import os
import time
from datetime import datetime
from pathlib import Path

from ralph.skills import upstream

upstream.RELEASES_API_URL = os.environ["RALPH_TEST_RELEASES_URL"]
upstream.ARCHIVE_URL_TEMPLATE = os.environ["RALPH_TEST_ARCHIVE_TEMPLATE"]

gate = Path(os.environ["RALPH_TEST_GATE"])
(gate / f"ready-{os.getpid()}").write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 30.0
while not (gate / "go").exists():
    if time.monotonic() > deadline:
        raise SystemExit("the start gate never opened")
    time.sleep(0.002)

outcome = upstream.maybe_update_universal_skills(
    enabled=True,
    now=datetime.fromisoformat(os.environ["RALPH_TEST_NOW"]),
    http=upstream.default_http_client(),
)
print(outcome.status, outcome.attempted)
'''

#: The companion files every universal skill of the fixture release carries.
_SKILL_FILES: dict[str, dict[str, bytes]] = {
    "test-driven-development": {
        "SKILL.md": b"# Test driven development\n",
        "test-first.md": b"# Test first\n",
    },
    "systematic-debugging": {
        "SKILL.md": b"# Systematic debugging\n",
        "condition-based-waiting.md": b"# Condition based waiting\n",
        "condition-based-waiting-example.ts": b"export const wait = (): boolean => true;\n",
    },
    "verification-before-completion": {
        "SKILL.md": b"# Verification before completion\n",
    },
}


@dataclass
class _Reply:
    """One scripted answer of the loopback test server.

    Attributes:
        status: The HTTP status to answer with.
        body: The body to answer with.
        headers: The extra headers to answer with.
        chunk_size: When positive the body is written in chunks of this size
            with a chunked transfer encoding, so the body carries no
            ``Content-Length``.
        stall: When set the server waits for this event before it writes the
            NEXT chunk. An event that is never set models an upstream whose
            body stopped mid-transfer.
    """

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    chunk_size: int = 0
    stall: threading.Event | None = None


class _Handler(BaseHTTPRequestHandler):
    """The request handler of the loopback test server."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        """Answer one request with the scripted reply."""
        server = self.server
        assert isinstance(server, _TestServer)
        server.requests.append(self.path)
        server.hosts.append(self.headers.get("Host", ""))
        self._send(server.responder(self.path))

    def _send(self, reply: _Reply) -> None:
        """Write the scripted reply to the client."""
        self.send_response(reply.status)
        for key, value in reply.headers.items():
            self.send_header(key, value)
        if reply.chunk_size > 0:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._send_chunked(reply)
            return
        self.send_header("Content-Length", str(len(reply.body)))
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(reply.body)

    def _send_chunked(self, reply: _Reply) -> None:
        """Write the body in chunks, stalling between two chunks when asked."""
        for start in range(0, len(reply.body), reply.chunk_size):
            chunk = reply.body[start : start + reply.chunk_size]
            try:
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk + b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            if reply.stall is not None and not reply.stall.wait(_STALL_TIMEOUT_SECONDS):
                return
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(b"0\r\n\r\n")

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr access log."""


class _TestServer(ThreadingHTTPServer):
    """The loopback server that carries the scripted answers."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, responder: Callable[[str], _Reply]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.responder = responder
        self.requests: list[str] = []
        self.hosts: list[str] = []

    @property
    def base_url(self) -> str:
        """Return the loopback base URL of the server."""
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


def _routes(routes: Mapping[str, _Reply]) -> Callable[[str], _Reply]:
    """Return a responder that answers from ``routes``."""

    def _respond(path: str) -> _Reply:
        return routes.get(path, _Reply(status=404, body=b"not found"))

    return _respond


@contextmanager
def _serving(responder: Callable[[str], _Reply]) -> Iterator[_TestServer]:
    """Serve ``responder`` on a loopback port for the duration of the block."""
    server = _TestServer(responder)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)
        _assert_loopback_hosts(server)


def _await_children(gate: Path, count: int, timeout: float = 30.0) -> None:
    """Wait until ``count`` children announced that they reached the gate."""
    deadline = time.monotonic() + timeout
    while len(list(gate.glob("ready-*"))) < count:
        if time.monotonic() > deadline:
            raise AssertionError("a child never reached the start gate")
        time.sleep(0.002)


def _await_quiet_server(server: _TestServer, seconds: float = 0.4) -> None:
    """Give the children a bounded window to reach the upstream.

    The window is short on purpose: a child that ignores the advisory lock
    of the attempt cache reaches the upstream within milliseconds, so the
    window catches it.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.01)


def _assert_loopback_hosts(server: _TestServer) -> None:
    """Fail the test when the server answered a request for a foreign host."""
    for host in server.hosts:
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host[1:].split("]")[0]
        assert name in _LOOPBACK_HOSTS, host


def _assert_loopback_url(url: str) -> None:
    """Fail the test when ``url`` points at a non-loopback host."""
    assert urlsplit(url).hostname in _LOOPBACK_HOSTS, url


def _point_at(server: _TestServer, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the updater at the loopback server."""
    releases = f"{server.base_url}/releases/latest.json"
    archive = f"{server.base_url}/archive/{{tag}}.tar.gz"
    _assert_loopback_url(releases)
    _assert_loopback_url(archive)
    monkeypatch.setattr(upstream, "RELEASES_API_URL", releases)
    monkeypatch.setattr(upstream, "ARCHIVE_URL_TEMPLATE", archive)


def _release_body(tag: str) -> bytes:
    """Return the release payload the upstream API answers with."""
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(tag: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
    """Return the archive bytes of a release carrying ``skills``."""
    root = f"superpowers-{tag.lstrip('v')}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for skill, files in skills.items():
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{root}/skills/{skill}/{relative}")
                info.size = len(data)
                info.mode = 0o755 if relative.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _client_for(
    tag: str, skills: Mapping[str, Mapping[str, bytes]]
) -> tuple[HttpClient, list[str]]:
    """Return a fake client serving one release, plus its request log."""
    calls: list[str] = []
    responses = {
        upstream.RELEASES_API_URL: (200, _release_body(tag)),
        upstream.ARCHIVE_URL_TEMPLATE.format(tag=tag): (
            200,
            _archive_bytes(tag, skills),
        ),
    }

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        return responses.get(url, (404, b""))

    return (_get, calls)


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Return a content snapshot of every file below ``root``."""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = "symlink:" + path.readlink().as_posix()
            continue
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user-global roots, the cache and the state file at a sandbox."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def _install_fixture_release() -> Path:
    """Install the fixture release through the injected seam and return the root."""
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    assert outcome.status == "updated"
    return upstream.canonical_skills_root()


def test_a_redirect_to_another_origin_is_refused_without_contacting_it(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _serving(_routes({})) as second:
        location = f"{second.base_url}/releases/latest.json"
        replies = _routes(
            {"/releases/latest.json": _Reply(status=302, headers={"Location": location})}
        )
        with _serving(replies) as first:
            _point_at(first, monkeypatch)
            outcome = upstream.maybe_update_universal_skills(
                enabled=True, now=_NOW, http=default_http_client()
            )
    assert second.requests == []
    assert outcome.status == "failed"
    assert "redirect" in (outcome.error or "")
    assert not (upstream.canonical_skills_root() / "test-driven-development").exists()


def test_a_body_that_grows_past_the_cap_is_refused_during_the_transfer(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    before = _tree_snapshot(root)
    monkeypatch.setattr(upstream, "MAX_ARCHIVE_BYTES", 64 * 1024)
    replies = _routes(
        {
            "/releases/latest.json": _Reply(body=_release_body("v-test-2")),
            "/archive/v-test-2.tar.gz": _Reply(body=b"x" * (256 * 1024), chunk_size=4096),
        }
    )
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=True,
            now=_NOW + timedelta(hours=25),
            http=default_http_client(),
        )
    assert outcome.status == "failed"
    assert "too-large" in (outcome.error or "")
    assert "timeout" not in (outcome.error or "").lower()
    assert _tree_snapshot(root) == before


def test_a_declared_length_above_the_cap_is_refused_before_the_body_is_read(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upstream, "MAX_RELEASE_BYTES", 1024)
    replies = _routes(
        {"/releases/latest.json": _Reply(body=b"z" * 4096)},
    )
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=True, now=_NOW, http=default_http_client()
        )
    assert outcome.status == "failed"
    assert "too-large" in (outcome.error or "")


def test_a_slow_body_hits_the_timeout_and_keeps_the_installed_skills(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    before = _tree_snapshot(root)
    monkeypatch.setattr(upstream, "REQUEST_TIMEOUT_SECONDS", 0.5)
    replies = _routes(
        {
            "/releases/latest.json": _Reply(body=_release_body("v-test-2")),
            "/archive/v-test-2.tar.gz": _Reply(
                body=b"y" * (64 * 1024), chunk_size=1024, stall=threading.Event()
            ),
        }
    )
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=True,
            now=_NOW + timedelta(hours=25),
            http=default_http_client(),
        )
    assert outcome.status == "failed"
    assert "timeout" in (outcome.error or "").lower()
    assert _tree_snapshot(root) == before


def test_a_server_error_fails_soft(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replies = _routes({"/releases/latest.json": _Reply(status=500, body=b"boom")})
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=True, now=_NOW, http=default_http_client()
        )
    assert outcome.status == "failed"
    assert outcome.error is not None


def test_a_malformed_release_body_fails_soft(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = _routes({"/releases/latest.json": _Reply(body=b"{not json")})
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=True, now=_NOW, http=default_http_client()
        )
    assert outcome.status == "failed"
    assert outcome.error is not None


def test_the_disabled_switch_makes_no_request(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = _routes({"/releases/latest.json": _Reply(body=_release_body("v-test-1"))})
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=False, now=_NOW, http=default_http_client()
        )
    assert outcome.status == "disabled"
    assert outcome.attempted is False
    assert server.requests == []


def test_the_attempt_reservation_leaves_a_lock_sidecar(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    lock = upstream.attempt_lock_path()
    assert lock.is_file()
    assert lock.parent == upstream.attempt_cache_path().parent


@pytest.mark.timeout_seconds(30)
def test_two_racing_processes_make_exactly_one_upstream_request(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two PROCESSES that share one cache make exactly ONE upstream attempt.

    Both children wait for a shared START GATE, so the reservation is
    genuinely contested. The parent holds the advisory lock of the attempt
    cache while it opens the gate: a child cannot reach the upstream while
    that lock is held, which is what makes the proof sensitive to the lock
    rather than to the order in which the children happen to start.
    """
    replies = _routes(
        {
            "/releases/latest.json": _Reply(body=_release_body("v-test-1")),
            "/archive/v-test-1.tar.gz": _Reply(status=404, body=b"no archive"),
        }
    )
    gate = tmp_path / "gate"
    gate.mkdir()
    script = tmp_path / "runner.py"
    script.write_text(_RUNNER, encoding="utf-8")
    lock_path = upstream.attempt_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with _serving(replies) as server:
            env = dict(os.environ)
            env["RALPH_TEST_RELEASES_URL"] = f"{server.base_url}/releases/latest.json"
            env["RALPH_TEST_ARCHIVE_TEMPLATE"] = f"{server.base_url}/archive/{{tag}}.tar.gz"
            env["RALPH_TEST_NOW"] = _NOW.isoformat()
            env["RALPH_TEST_GATE"] = str(gate)
            env["PYTHONPATH"] = str(_PACKAGE_ROOT)
            processes = [
                subprocess.Popen(
                    [sys.executable, str(script)],
                    env=env,
                    cwd=str(tmp_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(2)
            ]
            try:
                _await_children(gate, 2)
                (gate / "go").write_text("go", encoding="utf-8")
                _await_quiet_server(server)
                assert server.requests == []
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                outputs = [process.communicate(timeout=60) for process in processes]
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
        release_requests = [path for path in server.requests if path == "/releases/latest.json"]
        assert len(release_requests) == 1
        statuses = [stdout.strip() for stdout, _stderr in outputs]
        assert sorted(statuses) == ["failed True", "skipped False"]
    finally:
        os.close(descriptor)


def test_the_updater_never_raises_on_a_transport_failure(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replies = _routes({"/releases/latest.json": _Reply(status=404, body=b"")})
    with _serving(replies) as server:
        _point_at(server, monkeypatch)
        outcome: UpdateOutcome = upstream.maybe_update_universal_skills(
            enabled=True, now=_NOW, http=default_http_client()
        )
    assert outcome.status == "failed"
