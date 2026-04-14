"""Rust-specific review guideline categories.

Ported from the canonical Rust implementation in
``ralph-workflow/src/guidelines/rust.rs`` and adapted for the Python port.
The module exposes a lightweight data container that review prompt builders
can consume directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RustGuidelines:
    """Rust language-specific review checks.

    The categories mirror the review guideline structure used by the Rust
    implementation while including Rust-specific guidance around ownership,
    lifetimes, Clippy, panic-safety, and framework-oriented web handlers.
    """

    quality_checks: list[str] = field(
        default_factory=lambda: [
            "Code follows consistent style and formatting",
            "Functions have single responsibility",
            "Error handling is comprehensive",
            "No dead code or unused imports",
            "Run clippy and address actionable warnings",
            "No unwrap/expect in production paths; use Result + ?",
            "Proper lifetime annotations where needed",
            "Prefer borrowing over cloning",
            "Use strong types and exhaustive matching",
            "Keep public API minimal (pub(crate) by default)",
            "Use extractors for request data in web handlers",
            "Handle errors with proper status codes",
            "Use async handlers appropriately",
        ]
    )
    security_checks: list[str] = field(
        default_factory=lambda: [
            "No hardcoded secrets or credentials",
            "Input validation on external data",
            "Proper authentication/authorization checks",
            "Minimize unsafe code blocks; justify each use",
            "Check for integer overflow in arithmetic",
            "Validate untrusted input before processing",
            "Validate all user input",
            "Use tower middleware or equivalent for common web concerns",
        ]
    )
    performance_checks: list[str] = field(
        default_factory=lambda: [
            "No obvious performance bottlenecks",
            "Efficient data structures used",
            "Avoid unnecessary allocations (String → &str, Vec → slice)",
            "Use iterators instead of indexing loops",
            "Consider async for I/O-bound operations",
        ]
    )
    testing_checks: list[str] = field(
        default_factory=lambda: [
            "Tests cover main functionality",
            "Edge cases are tested",
            "Unit tests for core logic (#[cfg(test)])",
            "Integration tests in tests/ directory",
            "Consider property-based testing for invariants",
        ]
    )
    documentation_checks: list[str] = field(
        default_factory=lambda: [
            "Public APIs are documented",
            "Complex logic has explanatory comments",
            "Unsafe invariants and panic conditions are documented",
        ]
    )
    idioms: list[str] = field(
        default_factory=lambda: [
            "Code follows language conventions",
            "Follow Rust API Guidelines",
            "Use derive macros appropriately",
            "Implement standard traits (Debug, Clone, etc.) when justified",
        ]
    )
    anti_patterns: list[str] = field(
        default_factory=lambda: [
            "Avoid code duplication",
            "Avoid .clone() to satisfy the borrow checker without understanding ownership",
            "Do not reach for Rc<RefCell<T>> when ownership can be restructured",
            "Avoid panic! in library code",
        ]
    )
    concurrency_checks: list[str] = field(
        default_factory=lambda: [
            "Shared mutable state is properly synchronized",
            "No potential deadlocks from inconsistent lock ordering",
            "Spawned tasks respect Send/Sync requirements and cancellation paths",
        ]
    )
    resource_checks: list[str] = field(
        default_factory=lambda: [
            "Resources are properly closed or released on all paths",
            "No resource leaks in error paths",
            "Long-lived buffers, file handles, and sockets have clear ownership",
        ]
    )
    observability_checks: list[str] = field(
        default_factory=lambda: [
            "Errors are logged with context",
            "Critical operations have appropriate logging",
            "Structured logs do not omit error causes or request identifiers",
        ]
    )
    secrets_checks: list[str] = field(
        default_factory=lambda: [
            "Secrets loaded from environment/config, not hardcoded",
            "Sensitive data not logged or exposed in errors",
            "Credential material is zeroized or scoped tightly when feasible",
        ]
    )
    api_design_checks: list[str] = field(
        default_factory=lambda: [
            "API follows consistent naming conventions",
            "Breaking changes are clearly documented",
            "Return Result for recoverable failures instead of panicking",
            "Ownership, borrowing, and mutability are explicit in public APIs",
        ]
    )


__all__ = ["RustGuidelines"]
