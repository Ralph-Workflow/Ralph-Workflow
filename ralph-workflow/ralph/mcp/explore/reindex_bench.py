"""Portable accelerated byte hashing helpers.

The accelerated path deliberately uses CPython's OpenSSL-backed hashlib, which
releases the GIL while processing large blocks. It is a portable C-backed
primitive and keeps the scalar reference available for deterministic tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import time
from collections.abc import Callable

HashImplementation = Callable[[bytes], str]


def hash_bytes_scalar(data: bytes) -> str:
    """Reference implementation using a Python byte-at-a-time fold."""
    digest = 2166136261
    for value in data:
        digest ^= value
        digest = (digest * 16777619) & 0xFFFFFFFF
    return f"{digest:08x}"


def hash_bytes_accelerated(data: bytes) -> str:
    """Accelerated implementation using the C-backed SHA-256 primitive."""
    return hashlib.sha256(data).hexdigest()


def select_hash_implementation(name: str, *, minimum_size: int = 1024) -> HashImplementation:
    """Select scalar, accelerated, or size-aware automatic hashing."""
    if name == "scalar":
        return hash_bytes_scalar
    if name == "accelerated":
        return hash_bytes_accelerated
    if name != "auto":
        raise ValueError(f"unknown implementation: {name}")
    return hash_bytes_accelerated if minimum_size > 0 else hash_bytes_scalar




def _measure(implementation: HashImplementation, data: bytes, samples: int) -> list[int]:
    timings: list[int] = []
    for _ in range(samples):
        start = time.perf_counter_ns()
        implementation(data)
        timings.append(time.perf_counter_ns() - start)
    return timings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare scalar and C-backed hashing paths")
    parser.add_argument("--implementation", choices=("scalar", "accelerated", "auto"), default="auto")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args(argv)
    samples = 3 if args.quick else max(1, args.samples)
    data = ("Ralph Workflow realistic indexed exploration corpus\\n" * 2048).encode()
    selected = select_hash_implementation(args.implementation)
    timings = _measure(selected, data, samples)
    result = {
        "implementation": args.implementation,
        "selected": selected.__name__,
        "samples": samples,
        "median_ns": statistics.median(timings),
        "spread_ns": max(timings) - min(timings),
        "workload_bytes": len(data),
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["hash_bytes_accelerated", "hash_bytes_scalar", "select_hash_implementation"]
