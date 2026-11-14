import subprocess
import sys

import pytest


@pytest.mark.subprocess_e2e
@pytest.mark.parametrize(
    "imports",
    (
        "import ralph.agents.catalog; import ralph.agents.invoke",
        "import ralph.agents.invoke; import ralph.agents.catalog",
        "import ralph.agents.catalog; import ralph.agents.registry",
        "import ralph.agents.registry; import ralph.agents.catalog",
        "import ralph.agents.parsers.content_channel; import ralph.agents.execution_state",
        "import ralph.agents.execution_state; import ralph.agents.parsers.content_channel",
    ),
    ids=(
        "catalog-first-CIRCFIX",
        "invoke-first-CIRCFIX",
        "catalog-then-registry-CIRCFIX",
        "registry-then-catalog-CIRCFIX",
        "content-channel-then-execution-state-CIRCFIX",
        "execution-state-then-content-channel-CIRCFIX",
    ),
)
def test_agents_catalog_regression_import_orders_succeed(imports: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", imports],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
