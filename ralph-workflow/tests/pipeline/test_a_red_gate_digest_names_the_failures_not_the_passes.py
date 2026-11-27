from __future__ import annotations

from pathlib import Path

from ralph.pipeline.publication_gate import PublicationGateRun, check_publication_gate

_OUTPUT = "\n".join(
    (
        "PASS: Unknown axis and button calls record 5 error entries",
        "PASS: Story 16 logging run records 2 failures",
        "PASS: GET 404 error contains 404",
        "ok 12 - save migration survives 3 errors in a row",
        "Failed: MyGame.EditTests.LoggingTests.LogsUnexpectedErrorFails",
        "make: *** [verify] Error 1",
    )
)


class _Gate:
    def __call__(self, command: str, cwd: object, timeout_seconds: float) -> PublicationGateRun:
        del command, cwd, timeout_seconds
        return PublicationGateRun(exit_code=1, output=_OUTPUT)


def _declare(root: Path) -> None:
    policy = root / "docs" / "ralph-workflow-policy" / "verification-policy.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        "# Verification policy\n\nRALPH-FACT: authoritative_verify_command: `make verify`\n",
        encoding="utf-8",
    )


def test_the_digest_lists_the_failing_test_and_never_a_passing_one(tmp_path: Path) -> None:
    _declare(tmp_path)

    result = check_publication_gate(
        tmp_path, "HEAD", runner=_Gate(), tree_of=lambda _r, _s: "a" * 40
    )

    assert result is not None
    assert "LogsUnexpectedErrorFails" in result.failure_digest
    assert "PASS:" not in result.failure_digest
    assert "ok 12" not in result.failure_digest
