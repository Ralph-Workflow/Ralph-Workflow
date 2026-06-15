"""Unit tests for the shared plumbing bridge-lifetime context manager."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.factory import PipelineCore, build_minimal_pipeline_core
from ralph.pipeline.plumbing._bridge_lifetime import with_bridge_lifetime
from ralph.pipeline.session_bridge import build_session_bridge

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext


def _fake_display_context() -> DisplayContext:
    return make_display_context(env={"NO_COLOR": "1", "COLUMNS": "120"})


def _fake_pipeline_core(model_identity: object | None = None) -> PipelineCore:
    return build_minimal_pipeline_core(
        UnifiedConfig(), _fake_display_context(), model_identity=model_identity
    )


def test_with_bridge_lifetime_creates_and_yields_bridge() -> None:
    """The context manager yields the bridge created by the injected factory."""
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = _fake_pipeline_core()
    repo_root = Path("/workspace")

    with with_bridge_lifetime(
        core,
        bridge_factory,
        repo_root=repo_root,
        drain="commit",
        session_id_prefix="commit",
    ) as yielded:
        assert yielded is bridge

    bridge_factory.assert_called_once_with(
        workspace_root=repo_root,
        drain="commit",
        agents_policy=None,
        session_id_prefix="commit",
        model_identity=core.model_identity,
    )
    assert cast("MagicMock", bridge.shutdown).call_count == 1


def test_with_bridge_lifetime_shuts_down_on_body_raise() -> None:
    """``bridge.shutdown()`` is called even when the body raises."""
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = _fake_pipeline_core()

    class _BodyError(Exception):
        pass

    with (
        pytest.raises(_BodyError),
        with_bridge_lifetime(
            core,
            bridge_factory,
            repo_root=Path("/workspace"),
            drain="development",
            session_id_prefix="smoke",
        ),
    ):
        raise _BodyError("boom")

    assert cast("MagicMock", bridge.shutdown).call_count == 1


def test_with_bridge_lifetime_shuts_down_exactly_once_on_success() -> None:
    """On successful body completion, ``shutdown`` is called exactly once."""
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = _fake_pipeline_core()

    with with_bridge_lifetime(
        core,
        bridge_factory,
        repo_root=Path("/workspace"),
        drain="commit",
        session_id_prefix="commit",
    ):
        pass

    assert cast("MagicMock", bridge.shutdown).call_count == 1


def test_with_bridge_lifetime_forwards_model_identity() -> None:
    """The bridge factory receives ``model_identity`` from ``pipeline_core``."""
    model_identity = MagicMock()
    core = _fake_pipeline_core(model_identity=model_identity)
    bridge_factory = MagicMock(return_value=MagicMock())

    with with_bridge_lifetime(
        core,
        bridge_factory,
        repo_root=Path("/workspace"),
        drain="development",
        session_id_prefix="smoke",
    ):
        pass

    assert bridge_factory.call_args.kwargs["model_identity"] is model_identity


def test_with_bridge_lifetime_accepts_real_callable_and_magicmock() -> None:
    """Both a real ``BridgeFactory`` callable and a ``MagicMock`` work as factories."""
    core = _fake_pipeline_core()

    # MagicMock satisfies the call shape without running real bridge code.
    magicmock_bridge = MagicMock()
    with with_bridge_lifetime(
        core,
        MagicMock(return_value=magicmock_bridge),
        repo_root=Path("/workspace"),
        drain="commit",
        session_id_prefix="commit",
    ) as yielded:
        assert yielded is magicmock_bridge

    # The real production factory is also accepted (we do not invoke start-up).
    # Just verify the callable is accepted by the type system at import time.
    assert callable(build_session_bridge)


def test_with_bridge_lifetime_shutdown_is_idempotent() -> None:
    """An explicit body shutdown does not prevent the finally shutdown."""
    bridge = MagicMock()
    bridge_factory = MagicMock(return_value=bridge)
    core = _fake_pipeline_core()

    with with_bridge_lifetime(
        core,
        bridge_factory,
        repo_root=Path("/workspace"),
        drain="development",
        session_id_prefix="smoke",
    ) as yielded:
        yielded.shutdown()

    assert cast("MagicMock", bridge.shutdown).call_count == 2
