from __future__ import annotations

import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


def test_import_does_not_load_pipeline_state() -> None:
    code = textwrap.dedent(
        """
        import importlib
        import sys

        importlib.import_module('ralph.display.protocols')
        print('ralph.pipeline.state' in sys.modules)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_protocols_are_mypy_compatible(tmp_path: Path) -> None:
    mypy_api = pytest.importorskip("mypy.api")

    script = tmp_path / "check_protocols.py"
    script.write_text(
        textwrap.dedent(
            """
            from collections.abc import Mapping
            from typing import Literal, cast

            from rich.console import Console, RenderableType
            from rich.theme import Theme

            from ralph.display.protocols import (
                LayoutSelector,
                LayoutSpec,
                ModeSelectorProtocol,
                PanelRenderer,
                SubscriberProtocol,
            )
            from ralph.display.snapshot import DashboardSnapshot
            from ralph.pipeline.state import PipelineState

            class StubSubscriber:
                def notify(self, state: PipelineState) -> None:
                    pass

            subscriber: SubscriberProtocol = StubSubscriber()

            class StubPanelRenderer:
                name = 'stub'

                def render(
                    self,
                    snapshot: DashboardSnapshot,
                    *,
                    theme: Theme = Theme(),
                    width: int | None = None,
                ) -> RenderableType:
                    return cast(RenderableType, object())

            panel_renderer: PanelRenderer = StubPanelRenderer()

            class StubLayoutSelector:
                def __call__(
                    self,
                    snapshot: DashboardSnapshot,
                    *,
                    terminal_width: int,
                ) -> LayoutSpec:
                    return object()

                layout_selector: LayoutSelector = StubLayoutSelector()

            class StubModeSelector:
                def __call__(
                    self,
                    console: Console,
                    env: Mapping[str, str],
                ) -> Literal["dashboard", "lines"]:
                    return 'dashboard'

            mode_selector: ModeSelectorProtocol = StubModeSelector()
            """
        )
    )

    stdout, stderr, exit_status = mypy_api.run([str(script)])
    assert exit_status == 0, stdout + stderr
