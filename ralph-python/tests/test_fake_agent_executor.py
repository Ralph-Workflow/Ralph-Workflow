import asyncio
import pytest
from ralph.agents.executor import AgentExecutor, WorkerResult
from ralph.pipeline.work_units import WorkUnit
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun


def make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"Unit {unit_id}")


@pytest.mark.asyncio
async def test_protocol_isinstance() -> None:
    fake = FakeAgentExecutor({})
    assert isinstance(fake, AgentExecutor)


@pytest.mark.asyncio
async def test_returns_seeded_result() -> None:
    fake = FakeAgentExecutor(
        {
            "A": FakeRun(outputs=["line1", "line2"], exit_code=0, duration_ms=100),
        }
    )
    unit = make_unit("A")
    result = await fake.run(unit, on_output=lambda line: None, on_status=lambda s: None)
    assert result == WorkerResult(unit_id="A", exit_code=0, final_message="line2", duration_ms=100)


@pytest.mark.asyncio
async def test_records_outputs_emitted() -> None:
    outputs: list[str] = []
    fake = FakeAgentExecutor(
        {
            "B": FakeRun(outputs=["hello", "world"], exit_code=0, duration_ms=50),
        }
    )
    unit = make_unit("B")
    await fake.run(unit, on_output=lambda line: outputs.append(line), on_status=lambda s: None)
    assert outputs == ["hello", "world"]


@pytest.mark.asyncio
async def test_records_calls() -> None:
    fake = FakeAgentExecutor(
        {
            "C": FakeRun(outputs=[], exit_code=0, duration_ms=10),
        }
    )
    unit = make_unit("C")
    await fake.run(unit, on_output=lambda line: None, on_status=lambda s: None)
    assert len(fake.calls) == 1
    assert fake.calls[0] == unit


@pytest.mark.asyncio
async def test_raises_on_start() -> None:
    from ralph.agents.executor import ExecutorError

    fake = FakeAgentExecutor(
        {
            "D": FakeRun(
                outputs=[], exit_code=1, duration_ms=0, raise_on_start=ExecutorError("boom")
            ),
        }
    )
    unit = make_unit("D")
    with pytest.raises(ExecutorError, match="boom"):
        await fake.run(unit, on_output=lambda line: None, on_status=lambda s: None)


@pytest.mark.asyncio
async def test_nonzero_exit_in_result() -> None:
    fake = FakeAgentExecutor(
        {
            "E": FakeRun(outputs=["fail"], exit_code=1, duration_ms=50),
        }
    )
    unit = make_unit("E")
    result = await fake.run(unit, on_output=lambda line: None, on_status=lambda s: None)
    assert result.exit_code == 1


@pytest.mark.asyncio
async def test_empty_outputs_gives_empty_final_message() -> None:
    fake = FakeAgentExecutor(
        {
            "F": FakeRun(outputs=[], exit_code=0, duration_ms=10),
        }
    )
    unit = make_unit("F")
    result = await fake.run(unit, on_output=lambda line: None, on_status=lambda s: None)
    assert result.final_message == ""


@pytest.mark.asyncio
async def test_multiple_units_recorded() -> None:
    fake = FakeAgentExecutor(
        {
            "G": FakeRun(outputs=["g"], exit_code=0, duration_ms=10),
            "H": FakeRun(outputs=["h"], exit_code=0, duration_ms=10),
        }
    )
    await fake.run(make_unit("G"), on_output=lambda l: None, on_status=lambda s: None)
    await fake.run(make_unit("H"), on_output=lambda l: None, on_status=lambda s: None)
    assert len(fake.calls) == 2
    assert [u.unit_id for u in fake.calls] == ["G", "H"]
