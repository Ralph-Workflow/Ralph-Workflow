/// Context passed to event handlers containing shared state.
pub struct EventHandlerContext<'a> {
    pub colors: &'a crate::logger::Colors,
    pub verbosity: crate::config::Verbosity,
    pub display_name: &'a str,
    pub streaming_session:
        &'a std::rc::Rc<std::cell::RefCell<crate::json_parser::streaming_state::StreamingSession>>,
    pub reasoning_accumulator:
        &'a std::rc::Rc<std::cell::RefCell<crate::json_parser::types::DeltaAccumulator>>,
    pub terminal_mode: crate::json_parser::terminal::TerminalMode,
    pub show_streaming_metrics: bool,
    /// Track last rendered content for append-only streaming pattern
    pub last_rendered_content: &'a std::cell::RefCell<std::collections::HashMap<String, String>>,
}

impl<'a> EventHandlerContext<'a> {
    pub fn with_session_mut<R>(
        &self,
        f: impl FnOnce(&mut crate::json_parser::streaming_state::StreamingSession) -> R,
    ) -> R {
        f(&mut self.streaming_session.borrow_mut())
    }

    pub fn with_reasoning_accumulator_mut<R>(
        &self,
        f: impl FnOnce(&mut crate::json_parser::types::DeltaAccumulator) -> R,
    ) -> R {
        f(&mut self.reasoning_accumulator.borrow_mut())
    }

    pub fn with_last_rendered_content_mut<R>(
        &self,
        f: impl FnOnce(&mut std::collections::HashMap<String, String>) -> R,
    ) -> R {
        f(&mut self.last_rendered_content.borrow_mut())
    }
}
