from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.display.log_sink import make_stderr_log_sink

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from loguru import Logger

    class SinkAdder(Protocol):
        def __call__(self, target: object, /, **kwargs: object) -> int: ...


from .logging_models import LoggingConfig, LoggingPaths, LoggingSession
from .logging_worker_sink import WorkerSinkHandle, bind_worker_sink, remove_worker_sink

_FILE_SINK_BUFFER_BYTES: int = 8192

_VERBOSITY_LEVELS = {
    0: "ERROR",
    1: "WARNING",
    2: "INFO",
    3: "DEBUG",
    4: "TRACE",
}

_CUSTOM_LEVELS: tuple[tuple[str, int, str], ...] = (
    ("SUCCESS", 25, "<green>"),
    ("MILESTONE", 35, "<cyan>"),
)


def _ensure_custom_levels() -> None:
    for name, no, color in _CUSTOM_LEVELS:
        try:
            logger.level(name)
        except ValueError:
            logger.level(name, no=no, color=color)


def configure_logging(
    verbosity: int = 1,
    *,
    log_directory: str | Path | None = None,
    run_id: str | None = None,
    structured: bool = False,
    rotation: str | int | None = "10 MB",
    retention: str | int | None = "14 days",
    console_sink: Callable[[str], None] | None = None,
    sink_adder: SinkAdder | None = None,
) -> LoggingSession:
    config = LoggingConfig(
        verbosity=verbosity,
        log_directory=Path(log_directory) if log_directory is not None else None,
        run_id=run_id,
        structured=structured,
        rotation=rotation,
        retention=retention,
    )

    logger.remove()
    _ensure_custom_levels()

    level = _VERBOSITY_LEVELS.get(verbosity, "TRACE")
    bound_logger = logger.bind(**_build_base_extra(run_id))

    standard_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )

    sink = console_sink if console_sink is not None else make_stderr_log_sink()
    logger.add(
        sink,
        level=level,
        format=standard_format,
        colorize=False,
        backtrace=True,
        diagnose=False,
    )

    paths = _configure_file_handlers(config, level, sink_adder=sink_adder)
    session = LoggingSession(
        config=config,
        paths=paths,
        logger=bound_logger,
        ralph=RalphLogger(bound_logger),
    )

    session.logger.debug("Logging configured at {level} level", level=level)
    return session


def _build_base_extra(run_id: str | None) -> dict[str, str]:
    if run_id is None:
        return {}
    return {"run_id": run_id}


def _configure_file_handlers(
    config: LoggingConfig,
    level: str,
    *,
    sink_adder: SinkAdder | None = None,
) -> LoggingPaths:
    log_directory = config.log_directory
    if log_directory is None:
        return LoggingPaths(
            run_directory=None,
            text_log_path=None,
            structured_log_path=None,
        )

    run_directory = log_directory / config.run_id if config.run_id else log_directory
    run_directory.mkdir(parents=True, exist_ok=True)

    adder: SinkAdder = sink_adder if sink_adder is not None else cast("SinkAdder", logger.add)

    text_log_path = run_directory / "ralph.log"
    _add_buffered_file_sink(
        adder,
        text_log_path,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        colorize=False,
        backtrace=True,
        diagnose=False,
        rotation=config.rotation,
        retention=config.retention,
    )

    structured_log_path: Path | None = None
    if config.structured:
        structured_log_path = run_directory / "ralph.jsonl"
        assert structured_log_path is not None
        _add_buffered_file_sink(
            adder,
            structured_log_path,
            level=level,
            serialize=True,
            backtrace=True,
            diagnose=False,
            rotation=config.rotation,
            retention=config.retention,
        )

    return LoggingPaths(
        run_directory=run_directory,
        text_log_path=text_log_path,
        structured_log_path=structured_log_path,
    )


def _add_buffered_file_sink(adder: SinkAdder, path: Path, **kwargs: object) -> int:
    if "buffering" in kwargs:
        raise TypeError(
            "_add_buffered_file_sink sets buffering=8192 internally;"
            " callers must NOT pass buffering"
        )
    return adder(str(path), buffering=_FILE_SINK_BUFFER_BYTES, **kwargs)


def get_logger() -> Logger:
    return logger


class RalphLogger:
    def __init__(self, base_logger: Logger | None = None) -> None:
        self._logger: Logger = base_logger if base_logger is not None else logger

    def phase_start(self, phase: str, drain: str) -> None:
        self._logger.bind(event="phase_start", phase=phase, drain=drain).info(
            "Starting phase '{phase}' on drain '{drain}'",
            phase=phase,
            drain=drain,
        )

    def phase_complete(self, phase: str, drain: str) -> None:
        self._logger.bind(event="phase_complete", phase=phase, drain=drain).info(
            "Completed phase '{phase}' on drain '{drain}'",
            phase=phase,
            drain=drain,
        )

    def agent_invoked(self, agent_name: str, drain: str) -> None:
        self._logger.bind(event="agent_invoked", agent=agent_name, drain=drain).debug(
            "Invoking agent '{agent}' for drain '{drain}'",
            agent=agent_name,
            drain=drain,
        )

    def agent_output(self, drain: str, line: str) -> None:
        self._logger.bind(event="agent_output", drain=drain).debug(
            "agent_output | drain={drain} | line={line}",
            drain=drain,
            line=line,
        )

    def checkpoint_saved(self, path: str) -> None:
        self._logger.bind(event="checkpoint_saved", path=path).debug(
            "Checkpoint saved to '{path}'",
            path=path,
        )

    def checkpoint_loaded(self, path: str) -> None:
        self._logger.bind(event="checkpoint_loaded", path=path).debug(
            "Checkpoint loaded from '{path}'",
            path=path,
        )

    def policy_loaded(self, config_dir: str) -> None:
        self._logger.bind(event="policy_loaded", config_dir=config_dir).info(
            "Policy loaded from '{config_dir}'",
            config_dir=config_dir,
        )

    def validation_error(self, error: str) -> None:
        self._logger.bind(event="validation_error").error("Validation error: {error}", error=error)

    def pipeline_error(self, phase: str, error: str) -> None:
        self._logger.bind(event="pipeline_error", phase=phase).error(
            "Pipeline error in phase '{phase}': {error}",
            phase=phase,
            error=error,
        )

    def success(self, message: str, **extra: object) -> None:
        self._logger.bind(**extra).log("SUCCESS", message)

    def milestone(self, message: str, **extra: object) -> None:
        self._logger.bind(**extra).log("MILESTONE", message)


__all__ = [
    "RalphLogger",
    "WorkerSinkHandle",
    "bind_worker_sink",
    "configure_logging",
    "get_logger",
    "remove_worker_sink",
]
