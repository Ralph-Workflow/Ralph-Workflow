import pytest
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.parallel.scheduler import schedule_next_wave


def make_unit(unit_id: str, deps: list[str] | None = None) -> WorkUnit:
    return WorkUnit(
        unit_id=unit_id,
        description=f"Unit {unit_id}",
        dependencies=list(deps or []),
    )


def test_empty_dag() -> None:
    assert schedule_next_wave(set(), (), set(), max_workers=10) == []


def test_all_completed() -> None:
    units = (make_unit("A"), make_unit("B"))
    assert schedule_next_wave({"A", "B"}, units, set(), max_workers=10) == []


def test_fully_parallel_no_deps() -> None:
    units = (make_unit("A"), make_unit("B"), make_unit("C"))
    result = schedule_next_wave(set(), units, set(), max_workers=10)
    assert [u.unit_id for u in result] == ["A", "B", "C"]


def test_full_parallel_respects_cap() -> None:
    units = tuple(make_unit(str(i)) for i in range(5))
    result = schedule_next_wave(set(), units, set(), max_workers=2)
    assert len(result) == 2


def test_sequential_chain() -> None:
    units = (make_unit("A"), make_unit("B", ["A"]), make_unit("C", ["B"]))
    result = schedule_next_wave(set(), units, set(), max_workers=10)
    assert [u.unit_id for u in result] == ["A"]


def test_sequential_chain_advance() -> None:
    units = (make_unit("A"), make_unit("B", ["A"]), make_unit("C", ["B"]))
    result = schedule_next_wave({"A"}, units, set(), max_workers=10)
    assert [u.unit_id for u in result] == ["B"]


def test_diamond_dag() -> None:
    units = (
        make_unit("A"),
        make_unit("B", ["A"]),
        make_unit("C", ["A"]),
        make_unit("D", ["B", "C"]),
    )
    result = schedule_next_wave({"A"}, units, set(), max_workers=10)
    assert [u.unit_id for u in result] == ["B", "C"]


def test_diamond_dag_merge() -> None:
    units = (
        make_unit("A"),
        make_unit("B", ["A"]),
        make_unit("C", ["A"]),
        make_unit("D", ["B", "C"]),
    )
    result = schedule_next_wave({"A", "B", "C"}, units, set(), max_workers=10)
    assert [u.unit_id for u in result] == ["D"]


def test_running_units_excluded() -> None:
    units = (make_unit("A"), make_unit("B"))
    result = schedule_next_wave(set(), units, {"A"}, max_workers=10)
    assert [u.unit_id for u in result] == ["B"]


def test_max_workers_minus_running() -> None:
    units = (make_unit("A"), make_unit("B"), make_unit("C"))
    result = schedule_next_wave(set(), units, {"A"}, max_workers=2)
    assert len(result) == 1


def test_stable_ordering_by_unit_id() -> None:
    units = (make_unit("Z"), make_unit("A"), make_unit("M"))
    result = schedule_next_wave(set(), units, set(), max_workers=10)
    assert [u.unit_id for u in result] == ["A", "M", "Z"]


def test_no_slots_available() -> None:
    units = (make_unit("A"), make_unit("B"))
    result = schedule_next_wave(set(), units, {"X", "Y"}, max_workers=2)
    assert result == []
