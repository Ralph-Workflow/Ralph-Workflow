// Claude stream parsing methods.
//
// Contains event classification methods.

impl ClaudeParser {
    const fn is_control_event(event: &ClaudeEvent) -> bool {
        match event {
            // Stream events that are control events
            ClaudeEvent::StreamEvent { event } => matches!(
                event,
                StreamInnerEvent::MessageStart { .. }
                    | StreamInnerEvent::ContentBlockStart { .. }
                    | StreamInnerEvent::ContentBlockStop { .. }
                    | StreamInnerEvent::MessageDelta { .. }
                    | StreamInnerEvent::MessageStop
                    | StreamInnerEvent::Ping
            ),
            _ => false,
        }
    }

    /// Check if a Claude event is a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming content deltas (text deltas, thinking deltas,
    /// tool input deltas) that are shown to the user in real-time. These should be
    /// tracked separately to avoid inflating "ignored" percentages.
    const fn is_partial_event(event: &ClaudeEvent) -> bool {
        match event {
            // Stream events that produce incremental content
            ClaudeEvent::StreamEvent { event } => matches!(
                event,
                StreamInnerEvent::ContentBlockDelta { .. } | StreamInnerEvent::TextDelta { .. }
            ),
            _ => false,
        }
    }
}
