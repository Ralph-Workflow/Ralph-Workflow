import importlib

from ralph.pipeline.parallel.scheduler import schedule_next_wave
from ralph.pipeline.work_units import WorkUnit

MAX_SCHEDULED_RESULTS = 2

_hypothesis = importlib.import_module("hypothesis")
given = _hypothesis.given
settings = _hypothesis.settings
st = importlib.import_module("hypothesis.strategies")


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
    assert len(result) == MAX_SCHEDULED_RESULTS


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


def build_dag_units(
    num_units: int,
    dep_pairs: list[tuple[int, int]],
) -> tuple[WorkUnit, ...]:
    """Build a valid DAG from indices and dependency pairs, filtering cycles."""
    valid_deps: dict[int, list[str]] = {index: [] for index in range(num_units)}
    for src, dst in dep_pairs:
        if src < num_units and dst < num_units and src < dst:
            valid_deps[dst].append(f"unit-{src:02d}")

    return tuple(make_unit(f"unit-{index:02d}", valid_deps[index]) for index in range(num_units))


@settings(max_examples=12, database=None)
@given(
    num_units=st.integers(min_value=1, max_value=12),
    dep_pairs=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=11),
            st.integers(min_value=0, max_value=11),
        ),
        max_size=20,
    ),
    max_workers=st.integers(min_value=1, max_value=10),
)
def test_dag_property_no_deadlock_no_duplicate(
    num_units: int,
    dep_pairs: list[tuple[int, int]],
    max_workers: int,
) -> None:
    """Property: scheduler terminates, respects deps, and schedules each unit once."""
    units = build_dag_units(num_units, dep_pairs)
    actual_unit_ids = {unit.unit_id for unit in units}

    completed: set[str] = set()
    running: set[str] = set()
    all_scheduled: list[str] = []

    for _iteration in range(num_units + 1):
        ready = schedule_next_wave(completed, units, running, max_workers)

        for unit in ready:
            for dependency in unit.dependencies:
                if dependency in actual_unit_ids:
                    assert dependency in completed, (
                        f"Unit {unit.unit_id} returned but dep {dependency} not completed"
                    )

        for unit in ready:
            assert unit.unit_id not in all_scheduled, f"Unit {unit.unit_id} scheduled twice"
            assert unit.unit_id not in completed
            assert unit.unit_id not in running

        if not ready and not running:
            break

        for unit in ready:
            running.add(unit.unit_id)
            all_scheduled.append(unit.unit_id)

        for unit_id in list(running):
            completed.add(unit_id)
        running.clear()

    assert len(all_scheduled) == num_units, (
        f"Expected {num_units} scheduled, got {len(all_scheduled)}: {all_scheduled}"
    )
    assert set(all_scheduled) == actual_unit_ids
