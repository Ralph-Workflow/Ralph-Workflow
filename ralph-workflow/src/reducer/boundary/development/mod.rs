//! Development phase handler.
//!
//! The development phase is the core of the Ralph workflow, where an agent iteratively
//! implements the requirements from PROMPT.md according to the plan in PLAN.md.
//!
//! ## Phase Flow
//!
//! For each development iteration:
//!
//! 1. **Input Materialization** - Read PROMPT.md and PLAN.md, decide whether to inline or
//!    reference based on size budgets
//! 2. **Context Preparation** - Create backup files for large inputs
//! 3. **Prompt Preparation** - Generate the appropriate prompt based on mode (Normal, XSD Retry,
//!    Same-Agent Retry, or Continuation)
//! 4. **Agent Invocation** - Invoke the developer agent with the prepared prompt
//! 5. **XML Extraction** - Check for `development_result.xml` output
//! 6. **XML Validation** - Validate against XSD schema, extract `status/summary/files_changed`
//! 7. **XML Archival** - Move processed XML to .processed file
//! 8. **Outcome Application** - Transition to review phase if iteration completes
//!
//! ## Prompt Modes
//!
//! The development handler supports four prompt modes:
//!
//! - **Normal** - First attempt for an iteration, uses `developer_iteration_xml` template
//! - **XSD Retry** - Invalid XML output, includes XSD error context for correction
//! - **Same-Agent Retry** - Agent failed (non-XML issues), prepends retry preamble
//! - **Continuation** - Partial progress, includes continuation context from previous attempt
//!
//! ## Development Status
//!
//! The validated XML output determines the development status:
//!
//! - **Completed** - `<status>completed</status>` in XML, iteration finishes successfully
//! - **Partial** - `<status>partial</status>` in XML, triggers continuation
//! - **Failed** - `<status>failed</status>` or invalid XML, triggers retry or fallback
//!
//! ## Input Materialization Strategy
//!
//! PROMPT.md and PLAN.md are materialized based on size:
//!
//! - **Inline** - Content < 16KB, embedded directly in agent prompt
//! - **`FileReference`** - Content >= 16KB, referenced by path (agent reads from file)
//!
//! This prevents token budget exhaustion while preserving full context access.
//!
//! ## Architecture Compliance
//!
//! This handler is **impure** - it performs I/O via `ctx.workspace`. Key constraints:
//!
//! - **Never use `std::fs`** - Always use `ctx.workspace` for file operations
//! - **Emit facts, not decisions** - Events describe what happened (e.g., `XmlValidated`),
//!   not what to do (e.g., `RetryAgent`)
//! - **Single attempt per effect** - No hidden retry loops, orchestrator controls retries
//! - **Non-fatal writes** - Prompt file write failures log warnings but don't fail the pipeline
//!
//! See `docs/architecture/effect-system.md` for effect layer design principles.

mod core;
mod materialization;
mod preparation;
mod validation;

pub(super) use core::write_continuation_context_to_workspace;
