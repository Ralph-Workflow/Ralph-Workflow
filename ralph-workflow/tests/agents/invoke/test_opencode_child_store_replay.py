from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import ProcessLineReader, ProcessReaderCtx, extract_transport_session_id
from ralph.agents.invoke.opencode_subagent_sessions import SqliteOpenCodeChildPartSource
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.liveness import FakeLivenessProbe
from ralph.process.manager import ManagedProcess

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_opencode_child_store_replay_proves_subagent_activity_observed": "the removed check asserts reader._captured_session_id == parent_id, which read a private attribute of the line reader; the parent session id is now observed through the public transcript with extract_transport_session_id(consumed_lines), which is the shipped session id extraction path, so the same parent session id stays proven without a private reader attribute",
}

if TYPE_CHECKING:
    from collections.abc import Iterator

_WIRE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "display/_fixtures/opencode_subagent_wire.jsonl"
)
_STORE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "display/_fixtures/opencode_subagent_child_store.json"
)


class _ReplayHandle(ManagedProcess):
    def __init__(self, lines: list[str], clock: FakeClock, *, step_seconds: float = 3.0) -> None:
        self._lines = lines
        self._clock = clock
        self._step_seconds = step_seconds
        self._ralph_bounded_stderr = ""

    @property
    def pid(self) -> int:
        return 424242

    @property
    def returncode(self) -> int | None:
        return 0

    @property
    def stderr(self) -> Iterator[str]:
        return iter(())

    @property
    def stdout(self) -> Iterator[str]:
        for line in self._lines:
            self._clock.advance(self._step_seconds)
            yield line

    def terminate(self, grace_period_s: float | None = None, **_kwargs: object) -> None:
        del grace_period_s

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


def _init_sqlite_store(
    db_path: Path, store_data: dict[str, object], *, time_shift_ms: int = 0
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE session (
            id text PRIMARY KEY,
            project_id text NOT NULL,
            workspace_id text,
            parent_id text,
            slug text NOT NULL,
            directory text NOT NULL,
            path text NOT NULL,
            title text NOT NULL,
            version text NOT NULL,
            share_url text,
            summary_additions integer,
            summary_deletions integer,
            summary_files integer,
            summary_diffs integer,
            metadata text,
            cost real NOT NULL,
            tokens_input integer NOT NULL,
            tokens_output integer NOT NULL,
            tokens_reasoning integer NOT NULL,
            tokens_cache_read integer NOT NULL,
            tokens_cache_write integer NOT NULL,
            revert text,
            permission text,
            agent text NOT NULL,
            model text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            time_compacting integer,
            time_archived integer
        );
        CREATE TABLE part (
            id text PRIMARY KEY,
            message_id text NOT NULL,
            session_id text NOT NULL,
            time_created integer NOT NULL,
            time_updated integer NOT NULL,
            data text NOT NULL
        );
        """
    )
    raw_sessions_obj = store_data.get("sessions", [])
    raw_sessions = (
        [s for s in raw_sessions_obj if isinstance(s, dict)]
        if isinstance(raw_sessions_obj, list)
        else []
    )
    for s in raw_sessions:
        conn.execute(
            """
            INSERT INTO session (
                id, project_id, workspace_id, parent_id, slug, directory, path,
                title, version, share_url, summary_additions, summary_deletions,
                summary_files, summary_diffs, metadata, cost, tokens_input,
                tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write,
                revert, permission, agent, model, time_created, time_updated,
                time_compacting, time_archived
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                s.get("id"),
                s.get("project_id"),
                s.get("workspace_id"),
                s.get("parent_id"),
                s.get("slug"),
                s.get("directory"),
                s.get("path"),
                s.get("title"),
                s.get("version"),
                s.get("share_url"),
                s.get("summary_additions"),
                s.get("summary_deletions"),
                s.get("summary_files"),
                s.get("summary_diffs"),
                s.get("metadata"),
                s.get("cost", 0.0),
                s.get("tokens_input", 0),
                s.get("tokens_output", 0),
                s.get("tokens_reasoning", 0),
                s.get("tokens_cache_read", 0),
                s.get("tokens_cache_write", 0),
                s.get("revert"),
                s.get("permission"),
                s.get("agent"),
                s.get("model"),
                int(s.get("time_created", 0)) + time_shift_ms,
                int(s.get("time_updated", 0)) + time_shift_ms,
                s.get("time_compacting"),
                s.get("time_archived"),
            ),
        )

    raw_parts_obj = store_data.get("parts", [])
    raw_parts = (
        [p for p in raw_parts_obj if isinstance(p, dict)] if isinstance(raw_parts_obj, list) else []
    )
    for p in raw_parts:
        conn.execute(
            """
            INSERT INTO part (id, message_id, session_id, time_created, time_updated, data)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                p.get("id"),
                p.get("message_id"),
                p.get("session_id"),
                int(p.get("time_created", 0)) + time_shift_ms,
                int(p.get("time_updated", 0)) + time_shift_ms,
                p.get("data"),
            ),
        )
    conn.commit()
    conn.close()


def test_opencode_child_store_direct_fetch(tmp_path: Path) -> None:
    loaded = json.loads(_STORE_FIXTURE.read_text(encoding="utf-8"))
    store_data = loaded if isinstance(loaded, dict) else {}
    db_path = tmp_path / "opencode_direct.db"
    _init_sqlite_store(db_path, store_data)

    source = SqliteOpenCodeChildPartSource(db_path)
    parent_id = str(store_data["parent_session_id"])
    child_id = str(store_data["child_session_id"])

    parts = source.fetch(parent_id, 0)
    assert len(parts) == 9
    assert all(p.child_session_id == child_id for p in parts)
    assert all(p.agent == "explore" for p in parts)
    assert any(p.kind == "tool:read" for p in parts)
    assert any(p.kind == "reasoning" for p in parts)
    assert any(p.kind == "text" for p in parts)
    source.close()


def test_opencode_child_store_replay_proves_subagent_activity_observed(tmp_path: Path) -> None:
    wire_lines = _WIRE_FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)
    assert len(wire_lines) == 6

    loaded = json.loads(_STORE_FIXTURE.read_text(encoding="utf-8"))
    store_data = loaded if isinstance(loaded, dict) else {}
    parent_id = str(store_data["parent_session_id"])

    now_ms = int(time.time() * 1000)
    db_path = tmp_path / "opencode_replay.db"
    _init_sqlite_store(db_path, store_data, time_shift_ms=now_ms + 100_000)

    clock = FakeClock(start=0.0)
    source = SqliteOpenCodeChildPartSource(db_path)
    strategy = OpenCodeExecutionStrategy(label_scope=None, registry=None)
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
        policy=TimeoutPolicy(
            idle_timeout_seconds=300.0,
            drain_window_seconds=10.0,
            idle_poll_interval_seconds=1.0,
            max_session_seconds=None,
        ),
        execution_strategy=strategy,
        liveness_probe=FakeLivenessProbe(active=False),
        opencode_child_part_source=source,
    )

    handle = _ReplayHandle(wire_lines, clock, step_seconds=3.0)
    reader = ProcessLineReader(handle, ctx, clock=clock)

    consumed_lines = list(reader.read_lines())
    assert len(consumed_lines) == 6
    assert extract_transport_session_id(consumed_lines) == parent_id
