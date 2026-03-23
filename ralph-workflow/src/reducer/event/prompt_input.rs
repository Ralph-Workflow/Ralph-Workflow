//! Prompt input oversize detection and materialization events.
//!
//! These events make reducer-visible any transformation that affects the
//! agent-visible prompt content (inline vs file reference, truncation, etc.).

use crate::reducer::state::{MaterializedPromptInput, PromptInputKind};
use serde::{Deserialize, Serialize};

// Import from the parent event module, which defines PipelinePhase in mod.rs
// and ErrorEvent in error.rs
use crate::reducer::event::{ErrorEvent, PipelinePhase};

/// Prompt input oversize detection and materialization events.
///
/// These events make reducer-visible any transformation that affects the
/// agent-visible prompt content (inline vs file reference, truncation, etc.).
///
/// # Purpose
///
/// Large prompt inputs (PROMPT.md, PLAN.md, diffs) may exceed model context limits.
/// When this occurs, handlers materialize the content as file references instead of
/// inline text. These events record the materialization strategy for observability
/// and to enable the reducer to track content transformations.
///
/// # Emitted By
///
/// - Prompt preparation handlers in `handler/*/prepare_prompt.rs`
/// - XSD retry handlers
#[derive(Clone, Serialize, Deserialize, Debug)]
pub enum PromptInputEvent {
    /// Oversize content detected, will be materialized as file reference.
    OversizeDetected {
        /// Pipeline phase where oversize was detected.
        phase: PipelinePhase,
        /// Type of content (prompt, plan, diff, etc.).
        kind: PromptInputKind,
        /// SHA256 hex digest of the content.
        content_id_sha256: String,
        /// Actual content size in bytes.
        size_bytes: u64,
        /// Configured size limit in bytes.
        limit_bytes: u64,
        /// Materialization policy applied.
        policy: String,
    },
    /// Planning prompt inputs materialized.
    PlanningInputsMaterialized {
        /// Iteration number.
        iteration: u32,
        /// Materialized prompt input.
        prompt: MaterializedPromptInput,
    },
    /// Development prompt inputs materialized.
    DevelopmentInputsMaterialized {
        /// Iteration number.
        iteration: u32,
        /// Materialized prompt input.
        prompt: MaterializedPromptInput,
        /// Materialized plan input.
        plan: MaterializedPromptInput,
    },
    /// Review prompt inputs materialized.
    ReviewInputsMaterialized {
        /// Review pass number.
        pass: u32,
        /// Materialized plan input.
        plan: MaterializedPromptInput,
        /// Materialized diff input.
        diff: MaterializedPromptInput,
    },
    /// Commit prompt inputs materialized.
    CommitInputsMaterialized {
        /// Commit attempt number.
        attempt: u32,
        /// Materialized diff input.
        diff: MaterializedPromptInput,
    },
    /// XSD retry last output materialized.
    XsdRetryLastOutputMaterialized {
        /// Phase that produced the invalid output being retried.
        phase: PipelinePhase,
        /// Scope id within the phase (iteration/pass/attempt).
        scope_id: u32,
        /// Materialized representation of the last invalid output.
        last_output: MaterializedPromptInput,
    },
    /// A typed error event returned by an effect handler.
    ///
    /// Effect handlers surface failures by returning `Err(ErrorEvent::... .into())`.
    /// The event loop extracts the underlying `ErrorEvent` and re-emits it through
    /// this existing category so the reducer can decide recovery strategy without
    /// adding new top-level `PipelineEvent` variants.
    HandlerError {
        /// Phase during which the error occurred (best-effort; derived from current state).
        phase: PipelinePhase,
        /// The typed error event.
        error: ErrorEvent,
    },

    /// PROMPT.md permissions locked (read-only) at pipeline startup.
    ///
    /// Emitted by `LockPromptPermissions` effect handler when attempting to
    /// set PROMPT.md to read-only. If the operation fails, a warning is included
    /// but the pipeline continues (best-effort protection).
    PromptPermissionsLocked {
        /// Warning if permission change failed (None if successful or file missing).
        warning: Option<String>,
    },
    /// PROMPT.md permissions restore warning (best-effort).
    ///
    /// Emitted by `RestorePromptPermissions` effect handler when attempting to
    /// set PROMPT.md back to writable. The pipeline continues, but the warning
    /// is recorded for observability and resume diagnostics.
    PromptPermissionsRestoreWarning {
        /// Warning message when restore fails.
        warning: String,
    },

    /// Template was rendered, carrying substitution log.
    ///
    /// Emitted by prompt preparation handlers after template rendering.
    /// The substitution log enables validation based on tracked substitutions
    /// rather than regex scanning the rendered output.
    TemplateRendered {
        /// Pipeline phase during which the template was rendered.
        phase: PipelinePhase,
        /// Template name (e.g., "`commit_message_xml`").
        template_name: String,
        /// Detailed substitution log from rendering.
        log: crate::prompts::SubstitutionLog,
    },

    /// A prompt was generated and captured for future replay.
    ///
    /// Emitted by prompt preparation handlers when a fresh prompt is generated
    /// (not replayed from history). The reducer inserts the entry into
    /// `PipelineState::prompt_history` so subsequent effects and resumed runs
    /// can replay the same prompt deterministically.
    ///
    /// # RFC-007
    ///
    /// This event moves prompt history ownership from `PhaseContext` (mutable
    /// side-channel) into the pure event loop, ensuring that all history updates
    /// are observable, replayable, and consistent with reducer-owned state.
    PromptCaptured {
        /// Lookup key (from `PromptScopeKey::to_string()`).
        key: String,
        /// The generated prompt text.
        content: String,
        /// Optional SHA-256 content-id of the materialized inputs at generation time.
        ///
        /// When `Some`, future replay is gated on content-id matching to prevent
        /// stale-content replay after inputs change.
        content_id: Option<String>,
    },

    /// Gitignore entries ensured in the repository.
    ///
    /// Emitted by the `EnsureGitignoreEntries` effect handler after successfully
    /// ensuring that `.agent/` and `/PROMPT*` entries exist in `.gitignore`.
    /// The reducer sets `gitignore_entries_ensured = true` to prevent re-running
    /// the effect on subsequent iterations.
    ///
    /// Previously this was a `LifecycleEvent` variant but was moved here because
    /// it represents a prompt-input-related side effect, not a pipeline lifecycle
    /// control event.
    GitignoreEntriesEnsured {
        /// Entries that were added to .gitignore.
        added: Vec<String>,
        /// Entries that were already present.
        existing: Vec<String>,
        /// Whether .gitignore was created.
        created: bool,
    },
}
