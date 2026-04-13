//! Core types and builder methods for `MockEffectHandler`.
//!
//! This module contains the `MockEffectHandler` struct definition, its builder
//! pattern methods for configuration, and inspection helpers for verifying
//! captured effects and UI events.

use std::collections::VecDeque;

use super::io::CapturedState;
use super::{Effect, PipelineEvent, PipelineState, UIEvent};

/// Mock implementation of `EffectHandler` for testing.
///
/// This handler captures all executed effects for later inspection while
/// returning appropriate mock `PipelineEvents`. It performs NO real side effects:
/// - No git operations
/// - No file I/O
/// - No agent execution
/// - No subprocess spawning
///
/// # Examples
///
/// ```ignore
/// let state = PipelineState::initial(1, 0);
/// let mut handler = MockEffectHandler::new(state)
///     .with_empty_diff(); // Configure mock behavior
///
/// // Execute effects and verify
/// let result = handler.execute(effect, &mut ctx)?;
/// assert!(handler.was_effect_executed(|e| matches!(e, Effect::CreateCommit { .. })));
/// ```
pub struct MockEffectHandler {
    /// The pipeline state (updated by reducer, not handler).
    pub state: PipelineState,
    pub(super) captured_state: CapturedState,
    /// When true, `PrepareCommitPrompt` returns `CommitSkipped` instead of proceeding.
    pub(super) simulate_empty_diff: bool,

    /// Optional simulated error for `CheckCommitDiff`.
    pub(super) simulate_commit_diff_error: Option<String>,

    /// Optional simulated diff content for `CheckCommitDiff`.
    pub(super) simulate_commit_diff_content: Option<String>,

    /// Per-call staged diff contents for `CheckCommitDiff` (consumed in order, front first).
    ///
    /// When non-empty, the front entry takes priority over `simulate_commit_diff_content`
    /// and default diff content. Use `with_staged_diff_sequence` to configure.
    pub(super) staged_diff_contents: VecDeque<String>,

    /// Optional simulated commit JSON for `ValidateCommitXml`.
    pub(super) simulate_commit_json: Option<serde_json::Value>,

    /// Mock outcome for `CheckUncommittedChangesBeforeTermination`.
    pub(super) pre_termination_snapshot: PreTerminationSnapshotMock,

    /// Optional mock residual file outputs by pass.
    ///
    /// When set, `Effect::CheckResidualFiles { pass }` returns `ResidualFilesFound`
    /// with the configured paths (when non-empty) instead of always reporting clean.
    pub(super) residual_files_pass_1: Option<Vec<String>>,
    pub(super) residual_files_pass_2: Option<Vec<String>>,

    /// When true, the next call to `execute()` will panic.
    ///
    /// This supports integration tests that verify panic paths do not hang.
    pub(super) panic_on_next_execute: bool,

    /// Prompt keys to report as replayed (`was_replayed=true`) in `PromptReplayHit` events.
    ///
    /// When `PrepareCommitPrompt` (or other prompt preparation effects) fire, the mock
    /// emits `UIEvent::PromptReplayHit`. By default `was_replayed=false` (mock always
    /// generates fresh). Add keys here to simulate a resume scenario where those prompts
    /// were replayed from checkpoint history.
    pub(super) replay_prompt_keys: Option<std::collections::HashSet<String>>,

    /// Session override for capability gate testing.
    ///
    /// When set, this session is used instead of `ctx.active_session` for
    /// capability gate checks. This allows testing capability denial scenarios
    /// without requiring a full PhaseContext setup.
    pub(super) session_override: Option<crate::agents::session::AgentSession>,
}

#[derive(Debug, Clone)]
pub(super) enum PreTerminationSnapshotMock {
    Clean,
    Dirty {
        file_count: usize,
    },
    Error {
        kind: crate::reducer::event::WorkspaceIoErrorKind,
    },
}

impl MockEffectHandler {
    /// Create a new mock handler with the given initial state.
    ///
    /// # Arguments
    ///
    /// * `state` - Initial pipeline state to use
    ///
    /// # Returns
    ///
    /// A new `MockEffectHandler` with empty effect/event capture buffers
    #[must_use]
    pub fn new(state: PipelineState) -> Self {
        Self {
            state,
            captured_state: CapturedState::new(),
            simulate_empty_diff: false,
            simulate_commit_diff_error: None,
            simulate_commit_diff_content: None,
            staged_diff_contents: VecDeque::new(),
            simulate_commit_json: None,
            pre_termination_snapshot: PreTerminationSnapshotMock::Clean,
            residual_files_pass_1: None,
            residual_files_pass_2: None,
            panic_on_next_execute: false,
            replay_prompt_keys: None,
            session_override: None,
        }
    }

    /// Configure the mock to simulate empty diff scenario.
    ///
    /// When enabled, `CheckCommitDiff` effect returns a diff-empty event,
    /// causing the pipeline to skip commit message generation.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let handler = MockEffectHandler::new(state)
    ///     .with_empty_diff();
    /// ```
    #[must_use]
    pub const fn with_empty_diff(mut self) -> Self {
        self.simulate_empty_diff = true;
        self
    }

    /// Configure the mock to simulate a git diff error for `CheckCommitDiff`.
    #[must_use]
    pub fn with_commit_diff_error(mut self, message: impl Into<String>) -> Self {
        self.simulate_commit_diff_error = Some(message.into());
        self
    }

    /// Configure the mock to return a specific diff content for `CheckCommitDiff`.
    #[must_use]
    pub fn with_commit_diff_content(mut self, content: impl Into<String>) -> Self {
        self.simulate_commit_diff_content = Some(content.into());
        self
    }

    /// Configure a sequence of diff contents returned by successive `CheckCommitDiff` calls.
    ///
    /// Each call to `CheckCommitDiff` pops the front of this queue. This takes priority over
    /// `simulate_commit_diff_content` and the default diff. Use this when testing multi-iteration
    /// pipelines where each commit phase should receive distinct diff content.
    #[must_use]
    pub fn with_staged_diff_sequence(
        mut self,
        contents: impl IntoIterator<Item = impl Into<String>>,
    ) -> Self {
        self.staged_diff_contents = contents.into_iter().map(Into::into).collect();
        self
    }

    /// Configure the mock to use a specific commit JSON content for `ValidateCommitXml`.
    #[must_use]
    pub fn with_commit_json(mut self, json: serde_json::Value) -> Self {
        self.simulate_commit_json = Some(json);
        self
    }

    /// Mark a prompt key as replayed, causing `PrepareCommitPrompt` (and other prompt-prep
    /// effects) to emit `UIEvent::PromptReplayHit { was_replayed: true }` for that key.
    ///
    /// By default all prompts are emitted as `was_replayed: false` (fresh generation).
    /// Use this in resume tests where a specific prompt key should appear as a checkpoint
    /// replay.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let handler = MockEffectHandler::new(state)
    ///     .with_replay_prompt_key("commit_message_attempt_iter1_1");
    /// ```
    #[must_use]
    pub fn with_replay_prompt_key(self, key: impl Into<String>) -> Self {
        Self {
            replay_prompt_keys: Some(
                self.replay_prompt_keys
                    .iter()
                    .flatten()
                    .cloned()
                    .chain(std::iter::once(key.into()))
                    .collect(),
            ),
            ..self
        }
    }

    /// Configure the mock with a session override for capability gate testing.
    ///
    /// When set, this session is used instead of `ctx.active_session` for
    /// capability gate checks during `execute()`. This allows testing capability
    /// denial scenarios without requiring a full PhaseContext setup.
    ///
    /// # Examples
    ///
    /// ```ignore
    /// let session = AgentSession::for_drain("test".to_string(), SessionDrain::Planning, 1);
    /// let handler = MockEffectHandler::new(state)
    ///     .with_session_override(session);
    /// ```
    #[must_use]
    pub fn with_session_override(mut self, session: crate::agents::session::AgentSession) -> Self {
        self.session_override = Some(session);
        self
    }

    /// Configure the mock to simulate a clean working directory for the
    /// pre-termination safety check.
    #[must_use]
    pub const fn with_clean_pre_termination_snapshot(mut self) -> Self {
        self.pre_termination_snapshot = PreTerminationSnapshotMock::Clean;
        self
    }

    /// Configure the mock to simulate uncommitted changes for the pre-termination safety check.
    #[must_use]
    pub const fn with_dirty_pre_termination_snapshot(mut self, file_count: usize) -> Self {
        self.pre_termination_snapshot = PreTerminationSnapshotMock::Dirty { file_count };
        self
    }

    /// Configure residual file results for a specific commit pass.
    ///
    /// `pass` is 1 for the first selective-commit residual check. `pass >= 2` applies to
    /// the unattended retry loop; the mock currently stores one shared payload for all retry
    /// passes after the initial check.
    #[must_use]
    pub fn with_residual_files_for_pass<I, S>(mut self, pass: u8, files: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        let files: Vec<String> = files.into_iter().map(Into::into).collect();
        match pass {
            1 => self.residual_files_pass_1 = Some(files),
            2.. => self.residual_files_pass_2 = Some(files),
            _ => {}
        }
        self
    }

    /// Configure the mock to simulate a git status/snapshot failure for the pre-termination safety check.
    #[must_use]
    pub const fn with_pre_termination_snapshot_error(
        mut self,
        kind: crate::reducer::event::WorkspaceIoErrorKind,
    ) -> Self {
        self.pre_termination_snapshot = PreTerminationSnapshotMock::Error { kind };
        self
    }

    /// Configure the mock to panic on the next effect execution.
    ///
    /// This is used to test panic-unwind cleanup behavior in the event loop.
    #[must_use]
    pub const fn with_panic_on_next_execute(mut self) -> Self {
        self.panic_on_next_execute = true;
        self
    }

    /// Get all captured effects in execution order.
    ///
    /// Returns a clone of the captured effects vector. Effects are captured
    /// in the order they were executed.
    pub fn captured_effects(&self) -> Vec<Effect> {
        self.captured_state.effects.borrow().clone()
    }

    /// Get all captured UI events in emission order.
    ///
    /// Returns a clone of the captured UI events vector. UI events are captured
    /// in the order they were emitted by effect handlers.
    pub fn captured_ui_events(&self) -> Vec<UIEvent> {
        self.captured_state.ui_events.borrow().clone()
    }

    /// Get all captured pipeline events in emission order.
    pub fn captured_events(&self) -> Vec<PipelineEvent> {
        self.captured_state.events.borrow().clone()
    }

    /// Check if a specific effect type was captured.
    ///
    /// # Arguments
    ///
    /// * `predicate` - Function that returns `true` for matching effects
    ///
    /// # Examples
    ///
    /// ```ignore
    /// assert!(handler.was_effect_executed(|e|
    ///     matches!(e, Effect::CreateCommit { .. })
    /// ));
    /// ```
    pub fn was_effect_executed<F>(&self, predicate: F) -> bool
    where
        F: Fn(&Effect) -> bool,
    {
        self.captured_state.effects.borrow().iter().any(predicate)
    }

    /// Check if a specific UI event was emitted.
    ///
    /// # Arguments
    ///
    /// * `predicate` - Function that returns `true` for matching UI events
    ///
    /// # Examples
    ///
    /// ```ignore
    /// assert!(handler.was_ui_event_emitted(|e|
    ///     matches!(e, UIEvent::PhaseTransition { .. })
    /// ));
    /// ```
    pub fn was_ui_event_emitted<F>(&self, predicate: F) -> bool
    where
        F: Fn(&UIEvent) -> bool,
    {
        self.captured_state.ui_events.borrow().iter().any(predicate)
    }

    /// Check if a specific pipeline event was emitted.
    pub fn was_event_emitted<F>(&self, predicate: F) -> bool
    where
        F: Fn(&PipelineEvent) -> bool,
    {
        self.captured_state.events.borrow().iter().any(predicate)
    }

    /// Clear all captured effects and UI events.
    ///
    /// Useful for resetting the mock between test cases when reusing
    /// the same handler instance.
    pub fn clear_captured(&self) {
        self.captured_state.effects.borrow_mut().clear();
        self.captured_state.ui_events.borrow_mut().clear();
        self.captured_state.events.borrow_mut().clear();
    }

    /// Get the number of captured effects.
    pub fn effect_count(&self) -> usize {
        self.captured_state.effects.borrow().len()
    }

    /// Get the number of captured UI events.
    pub fn ui_event_count(&self) -> usize {
        self.captured_state.ui_events.borrow().len()
    }

    /// Get the number of captured pipeline events.
    pub fn event_count(&self) -> usize {
        self.captured_state.events.borrow().len()
    }
}
