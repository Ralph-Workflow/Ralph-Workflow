from enum import StrEnum


class AgentEntryPoint(StrEnum):
    MAIN_PHASE_LOOP = "main_phase_loop"
    PARALLEL_WORKER = "parallel_worker"
    INTEGRATION_CONTINUATION = "integration_continuation"
    CONFLICT_RESOLUTION = "conflict_resolution"
    COMMIT_GENERATION = "commit_generation"
    SMOKE = "smoke"
    PROJECT_POLICY = "project_policy"
    SESSION_RUNTIME = "session_runtime"
    CANARY_PROBE = "canary_probe"


__all__ = ("AgentEntryPoint",)
