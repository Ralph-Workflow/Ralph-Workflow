from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from typing import cast

_MAX_EVENTS = 32

_CandidateKey = tuple[str, str]
_CandidateRecord = dict[str, object]
_CandidateEntry = tuple[_CandidateKey, _CandidateRecord]


class FalseBenchFeedback:
    def __init__(self) -> None:
        self._count = 0
        self._events: tuple[dict[str, object], ...] = ()
        self._candidates: deque[_CandidateEntry] = deque(maxlen=32)

    @property
    def count(self) -> int:
        return self._count

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return deepcopy(self._events)

    def note_bench(
        self,
        phase: str,
        agent: str,
        reason: str,
        *,
        config_fingerprint: str,
        verdict_ref: Mapping[str, object],
        session_ref: Mapping[str, str],
    ) -> None:
        key = (agent, config_fingerprint)
        for index, (existing_key, _) in enumerate(self._candidates):
            if existing_key == key:
                del self._candidates[index]
                break
        self._candidates.append(
            (
                key,
                {
                    "agent": agent,
                    "phase": phase,
                    "reason": reason,
                    "config_fingerprint": config_fingerprint,
                    "verdict_ref": deepcopy(dict(verdict_ref)),
                    "session_ref": dict(session_ref),
                },
            )
        )

    def note_success(self, phase: str, agent: str, *, config_fingerprint: str) -> bool:
        del phase
        key = (agent, config_fingerprint)
        for index, (existing_key, _candidate) in enumerate(self._candidates):
            if existing_key == key:
                del self._candidates[index]
                break
        else:
            return False
        self._count += 1
        self._events = (*self._events, _candidate)[-_MAX_EVENTS:]
        return True

    def snapshot(self) -> dict[str, object]:
        return {
            "count": self._count,
            "events": deepcopy(list(self._events)),
            "candidates": deepcopy([record for _, record in self._candidates]),
        }

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, object] | None) -> FalseBenchFeedback:
        feedback = cls()
        if not isinstance(snapshot, Mapping):
            return feedback
        count = snapshot.get("count")
        events = snapshot.get("events")
        candidates = snapshot.get("candidates")
        if not isinstance(count, int) or count < 0 or not isinstance(events, list):
            return feedback
        parsed_events = [cls._parse_record(value) for value in events]
        if any(value is None for value in parsed_events):
            return feedback
        if not isinstance(candidates, list):
            return feedback
        parsed_candidates = [cls._parse_record(value) for value in candidates]
        if any(value is None for value in parsed_candidates):
            return feedback
        feedback._count = count
        feedback._events = tuple(
            value for value in parsed_events[-_MAX_EVENTS:] if value is not None
        )
        for value in parsed_candidates:
            if value is None:
                continue
            key = (str(value["agent"]), str(value["config_fingerprint"]))
            feedback._candidates.append((key, value))
        return feedback

    @staticmethod
    def _parse_record(value: object) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None
        required_strings = ("agent", "phase", "reason", "config_fingerprint")
        if any(not isinstance(value.get(key), str) for key in required_strings):
            return None
        verdict_ref = value.get("verdict_ref")
        session_ref = value.get("session_ref")
        if not isinstance(verdict_ref, Mapping) or not isinstance(session_ref, Mapping):
            return None
        parsed_verdict_ref: dict[str, object] = {}
        for key, item in cast("Mapping[object, object]", verdict_ref).items():
            if not isinstance(key, str):
                return None
            parsed_verdict_ref[key] = deepcopy(item)
        parsed_session_ref: dict[str, str] = {}
        for key, item in cast("Mapping[object, object]", session_ref).items():
            if not isinstance(key, str) or not isinstance(item, str):
                return None
            parsed_session_ref[key] = item
        return {
            "agent": value["agent"],
            "phase": value["phase"],
            "reason": value["reason"],
            "config_fingerprint": value["config_fingerprint"],
            "verdict_ref": parsed_verdict_ref,
            "session_ref": parsed_session_ref,
        }


__all__ = ["FalseBenchFeedback"]
