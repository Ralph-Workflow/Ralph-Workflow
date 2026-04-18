from ralph.pipeline.work_units import WorkUnit


def schedule_next_wave(
    completed: set[str],
    all_units: tuple[WorkUnit, ...],
    currently_running: set[str],
    max_workers: int,
) -> list[WorkUnit]:
    available_slots = max_workers - len(currently_running)
    if available_slots <= 0:
        return []

    ready = [
        unit
        for unit in all_units
        if unit.unit_id not in completed
        and unit.unit_id not in currently_running
        and all(dep in completed for dep in unit.dependencies)
    ]
    ready.sort(key=lambda u: u.unit_id)
    return ready[:available_slots]
