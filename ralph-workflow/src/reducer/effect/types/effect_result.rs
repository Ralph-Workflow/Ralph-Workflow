// NOTE: split from reducer/effect/types.rs (EffectResult struct).

use crate::reducer::event::PipelineEvent;
use crate::reducer::ui_event::UIEvent;

/// Result of executing an effect.
///
/// Contains both the `PipelineEvent` (for reducer) and optional `UIEvents` (for display).
/// This separation keeps UI concerns out of the reducer while allowing handlers
/// to emit rich feedback during execution.
///
/// # Multiple Events
///
/// Some effects produce multiple reducer events. For example, agent invocation
/// may produce:
/// 1. `InvocationSucceeded` - the primary event
/// 2. `SessionEstablished` - additional event when session ID is extracted
///
/// The `additional_events` field holds events that should be processed after
/// the primary event. The reducer loop processes all events in order.
#[derive(Clone, Debug)]
pub struct EffectResult {
    /// Primary event for reducer (affects state).
    pub event: PipelineEvent,
    /// Additional events to process after the primary event.
    ///
    /// Used for cases where an effect produces multiple events, such as
    /// agent invocation followed by session establishment. Each event is
    /// processed by the reducer in order.
    pub additional_events: Vec<PipelineEvent>,
    /// UI events for display (do not affect state).
    pub ui_events: Vec<UIEvent>,
}

impl EffectResult {
    /// Create result with just a pipeline event (no UI events).
    #[must_use]
    pub const fn event(event: PipelineEvent) -> Self {
        Self {
            event,
            additional_events: Vec::new(),
            ui_events: Vec::new(),
        }
    }

    /// Create result with pipeline event and UI events.
    #[must_use]
    pub const fn with_ui(event: PipelineEvent, ui_events: Vec<UIEvent>) -> Self {
        Self {
            event,
            additional_events: Vec::new(),
            ui_events,
        }
    }

    /// Add an additional event to process after the primary event.
    ///
    /// Used for emitting separate events like `SessionEstablished` after
    /// agent invocation completes. Each additional event is processed
    /// by the reducer in order.
    #[must_use]
    pub fn with_additional_event(self, event: PipelineEvent) -> Self {
        let additional_events = self
            .additional_events
            .into_iter()
            .chain(std::iter::once(event))
            .collect();
        Self {
            event: self.event,
            additional_events,
            ui_events: self.ui_events,
        }
    }

    /// Add a UI event to the result.
    #[must_use]
    pub fn with_ui_event(self, ui_event: UIEvent) -> Self {
        let ui_events = self
            .ui_events
            .into_iter()
            .chain(std::iter::once(ui_event))
            .collect();
        Self {
            event: self.event,
            additional_events: self.additional_events,
            ui_events,
        }
    }
}
