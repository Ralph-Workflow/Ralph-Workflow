from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from tests.recovery._recorded_attempt_fact import RecordedAttemptFact


class RecordingRecoveryController(RecoveryController):
    def __init__(self, *, options: RecoveryControllerOptions) -> None:
        super().__init__(options=options)
        self.recorded_facts: list[RecordedAttemptFact] = []

    def record_attempt_fact(
        self,
        phase: str,
        agent: str,
        *,
        artifact_valid: bool,
        workspace_changed: bool,
        free_retry_category: bool = False,
    ) -> str | None:
        self.recorded_facts.append(
            RecordedAttemptFact(
                phase=phase,
                agent=agent,
                artifact_valid=artifact_valid,
                workspace_changed=workspace_changed,
                free_retry_category=free_retry_category,
            )
        )
        return super().record_attempt_fact(
            phase,
            agent,
            artifact_valid=artifact_valid,
            workspace_changed=workspace_changed,
            free_retry_category=free_retry_category,
        )
