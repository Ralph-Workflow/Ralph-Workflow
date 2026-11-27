from __future__ import annotations

import os
import shutil
import tempfile
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.publication_gate_slots import GATE_SLOT_DIR_ENV

if TYPE_CHECKING:
    from collections.abc import Iterator

_SESSION_TEMP_PREFIX = "rpt-"


@pytest.fixture(scope="session", autouse=True)
def _contain_session_temp_files() -> Iterator[None]:
    previous_tempdir = tempfile.tempdir
    previous_env = os.environ.get("TMPDIR")
    session_root = tempfile.mkdtemp(prefix=_SESSION_TEMP_PREFIX)
    tempfile.tempdir = session_root
    os.environ["TMPDIR"] = session_root
    previous_slot_dir = os.environ.get(GATE_SLOT_DIR_ENV)
    os.environ[GATE_SLOT_DIR_ENV] = tempfile.mkdtemp(prefix="gate-slots-", dir=session_root)
    try:
        yield
    finally:
        if previous_slot_dir is None:
            os.environ.pop(GATE_SLOT_DIR_ENV, None)
        else:
            os.environ[GATE_SLOT_DIR_ENV] = previous_slot_dir
        tempfile.tempdir = previous_tempdir
        if previous_env is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous_env
        shutil.rmtree(session_root, ignore_errors=True)
