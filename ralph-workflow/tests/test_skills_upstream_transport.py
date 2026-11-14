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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

import pytest

from ralph.skills import upstream
from ralph.skills.upstream import HttpClient, HttpResponse, default_http_client

RALPH_TEST_CHANGE_REASONS: Final[dict[str, str]] = {
    "test_a_failing_upstream_keeps_the_installed_skills_byte_identical outcome.status": (
        "HTTP 404 is the explicit no-release outcome for a release endpoint; every scenario "
        "now retains exact status equality against its public outcome contract"
    ),
    "test_a_failing_upstream_keeps_the_installed_skills_byte_identical outcome.error": (
        "the no-release outcome intentionally has no error; every scenario now asserts exact "
        "error equality, which is stronger than the former non-None check for failures"
    ),
    "test_two_racing_processes_make_exactly_one_upstream_request release_requests": (
        "one winning process makes exactly one release request for each configured upstream; "
        "distinct source paths now retain an exact count-of-one assertion per upstream"
    ),
}

pytestmark = [pytest.mark.subprocess_e2e]

if TYPE_CHECKING:
    from ralph.skills.upstream import UpdateOutcome

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_STALL_TIMEOUT_SECONDS = 2.0

_PACKAGE_ROOT = Path(upstream.__file__).resolve().parents[2]

_RUNNER = '''"""Make one upstream attempt against the loopback test server."""

import os
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ralph.skills import upstream

releases = os.environ["RALPH_TEST_RELEASES_BASE"]
archive = os.environ["RALPH_TEST_ARCHIVE_BASE"]
upstream.UNIVERSAL_UPSTREAMS = tuple(
    replace(
        source,
        api_url=f"{releases}/{index}.json",
        archive_url_template=f"{archive}/{index}/{{tag}}.tar.gz",
    )
    for index, source in enumerate(upstream.UNIVERSAL_UPSTREAMS)
)

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
    "antislop": {
        "SKILL.md": b"# Antislop\n",
        "VERSION": b"3.2.30\n",
    },
}


@dataclass
class _Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    chunk_size: int = 0
    stall: threading.Event | None = None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _TestServer)
        server.requests.append(self.path)
        server.hosts.append(self.headers.get("Host", ""))
        self._send(server.responder(self.path))

    def _send(self, reply: _Reply) -> None:
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
        pass


class _TestServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, responder: Callable[[str], _Reply]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.responder = responder
        self.requests: list[str] = []
        self.hosts: list[str] = []

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


def _routes(routes: Mapping[str, _Reply]) -> Callable[[str], _Reply]:

    def _respond(path: str) -> _Reply:
        return routes.get(path, _Reply(status=404, body=b"not found"))

    return _respond


@contextmanager
def _serving(responder: Callable[[str], _Reply]) -> Iterator[_TestServer]:
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
    deadline = time.monotonic() + timeout
    while len(list(gate.glob("ready-*"))) < count:
        if time.monotonic() > deadline:
            raise AssertionError("a child never reached the start gate")
        time.sleep(0.002)


def _await_quiet_server(server: _TestServer, seconds: float = 0.4) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.01)


def _assert_loopback_hosts(server: _TestServer) -> None:
    for host in server.hosts:
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host[1:].split("]")[0]
        assert name in _LOOPBACK_HOSTS, host


def _assert_loopback_url(url: str) -> None:
    assert urlsplit(url).hostname in _LOOPBACK_HOSTS, url


def _point_at(server: _TestServer, monkeypatch: pytest.MonkeyPatch) -> None:
    releases = f"{server.base_url}/releases/latest.json"
    archive = f"{server.base_url}/archive/{{tag}}.tar.gz"
    _assert_loopback_url(releases)
    _assert_loopback_url(archive)
    monkeypatch.setattr(
        upstream,
        "UNIVERSAL_UPSTREAMS",
        tuple(
            replace(source, api_url=releases, archive_url_template=archive)
            for source in upstream.UNIVERSAL_UPSTREAMS
        ),
    )


def _point_at_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    releases = _unreachable_loopback_url()
    archive = releases.replace("/releases/latest.json", "/archive/{tag}.tar.gz")
    _assert_loopback_url(releases)
    _assert_loopback_url(archive)
    monkeypatch.setattr(
        upstream,
        "UNIVERSAL_UPSTREAMS",
        tuple(
            replace(source, api_url=releases, archive_url_template=archive)
            for source in upstream.UNIVERSAL_UPSTREAMS
        ),
    )


def _release_body(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(tag: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
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
    calls: list[str] = []
    responses = {
        upstream.UNIVERSAL_UPSTREAMS[0].api_url: (200, _release_body(tag)),
        upstream.UNIVERSAL_UPSTREAMS[0].archive_url_template.format(tag=tag): (
            200,
            _archive_bytes(tag, skills),
        ),
    }

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        return responses.get(url, (404, b""))

    return (_get, calls)


def _tree_snapshot(root: Path) -> dict[str, str]:
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
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def _install_fixture_release() -> Path:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    assert outcome.status == "updated"
    return upstream.canonical_skills_root()


def _unreachable_loopback_url() -> str:
    server = _TestServer(_routes({}))
    url = f"http://127.0.0.1:{server.server_address[1]}/releases/latest.json"
    server.server_close()
    return url


def _failing_replies(failure: str) -> dict[str, _Reply]:
    if failure == "rate-limited":
        return {"/releases/latest.json": _Reply(status=403, body=b"rate limited")}
    if failure == "missing-upstream":
        return {"/releases/latest.json": _Reply(status=404, body=b"not found")}
    if failure == "malformed-release":
        return {"/releases/latest.json": _Reply(body=b"{not json")}
    if failure == "malformed-archive":
        return {
            "/releases/latest.json": _Reply(body=_release_body("v-test-2")),
            "/archive/v-test-2.tar.gz": _Reply(body=b"this is not a tarball"),
        }
    raise AssertionError(f"unknown failure {failure!r}")


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_error"),
    [
        ("rate-limited", "failed", "release-api-status-403"),
        ("missing-upstream", "no-release", None),
        ("malformed-release", "failed", "release-api-malformed"),
        ("malformed-archive", "failed", "the archive could not be read: ReadError"),
        ("offline", "failed", "release-api-error:ConnectError"),
    ],
)
def test_a_failing_upstream_keeps_the_installed_skills_byte_identical(
    sandbox: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_status: str,
    expected_error: str | None,
) -> None:
    root = _install_fixture_release()
    before = _tree_snapshot(root)

    if failure == "offline":
        _point_at_unreachable(monkeypatch)
        outcome = upstream.maybe_update_universal_skills(
            enabled=True, now=_NOW + timedelta(hours=25), http=default_http_client()
        )
    else:
        with _serving(_routes(_failing_replies(failure))) as server:
            _point_at(server, monkeypatch)
            outcome = upstream.maybe_update_universal_skills(
                enabled=True, now=_NOW + timedelta(hours=25), http=default_http_client()
            )

    assert outcome.status == expected_status
    assert outcome.error == expected_error
    assert _tree_snapshot(root) == before


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
    release_paths = tuple(
        f"/releases/{index}.json" for index in range(len(upstream.UNIVERSAL_UPSTREAMS))
    )
    archive_paths = tuple(
        f"/archive/{index}/v-test-1.tar.gz" for index in range(len(upstream.UNIVERSAL_UPSTREAMS))
    )
    responses = {path: _Reply(body=_release_body("v-test-1")) for path in release_paths}
    responses.update({path: _Reply(status=404, body=b"no archive") for path in archive_paths})
    replies = _routes(responses)
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
            env["RALPH_TEST_RELEASES_BASE"] = f"{server.base_url}/releases"
            env["RALPH_TEST_ARCHIVE_BASE"] = f"{server.base_url}/archive"
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
        for path in release_paths:
            assert server.requests.count(path) == 1, (
                f"exactly one process must request {path}; got {server.requests!r}"
            )
        statuses = [stdout.strip() for stdout, _stderr in outputs]
        assert sorted(statuses) == ["failed True", "skipped False"]
    finally:
        os.close(descriptor)


def test_the_updater_never_raises_on_a_transport_failure(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _point_at_unreachable(monkeypatch)
    outcome: UpdateOutcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW, http=default_http_client()
    )
    assert outcome.status == "failed"
    assert outcome.error == "release-api-error:ConnectError"
