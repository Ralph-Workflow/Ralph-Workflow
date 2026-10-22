"""The one-agent conflict-resolution chain warning is emitted once per process."""

from __future__ import annotations

import uuid
from pathlib import Path

from loguru import logger

from ralph.policy.loader import load_policy
from tests._support.policy_dirs import policy_dir_with_resolver_chain

_LOADS = 4


def test_repeated_policy_loads_warn_about_a_single_agent_chain_once(tmp_path: Path) -> None:
    chain = f"solo_{uuid.uuid4().hex}"
    config_dir = policy_dir_with_resolver_chain(tmp_path, chain)
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        for _ in range(_LOADS):
            load_policy(config_dir)
    finally:
        logger.remove(sink_id)

    assert sum(chain in record for record in records) == 1


def test_a_different_single_agent_chain_still_warns(tmp_path: Path) -> None:
    first = f"solo_{uuid.uuid4().hex}"
    second = f"solo_{uuid.uuid4().hex}"
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        load_policy(policy_dir_with_resolver_chain(tmp_path / "a", first))
        load_policy(policy_dir_with_resolver_chain(tmp_path / "b", second))
    finally:
        logger.remove(sink_id)

    assert sum(first in record for record in records) == 1
    assert sum(second in record for record in records) == 1
