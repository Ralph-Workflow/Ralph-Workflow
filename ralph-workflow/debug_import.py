"""Debug script to see what pytest sees during import."""
import sys
print("Python path:")
for i, p in enumerate(sys.path):
    print(f"  {i}: {p}")

print("\nralph.recovery.controller in sys.modules:", 'ralph.recovery.controller' in sys.modules)

# Try importing what conftest does
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
print("policy.models imported OK")

from ralph.runtime import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    timeout_seconds_from_env,
)
print("runtime imported OK")

# Now try the controller import
from ralph.recovery.controller import (
    RecoveryController,
    RecoveryControllerOptions,
    compute_backoff_ms,
)
print("controller imported OK:", RecoveryControllerOptions)