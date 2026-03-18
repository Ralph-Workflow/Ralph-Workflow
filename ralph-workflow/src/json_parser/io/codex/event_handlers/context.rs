/// Context passed to event handlers containing shared state.
pub struct EventHandlerContext<'a> {
    pub colors: &'a Colors,
    pub verbosity: Verbosity,
    pub display_name: &'a str,
    pub streaming_session: &'a Rc<RefCell<StreamingSession>>,
    pub reasoning_accumulator: &'a Rc<RefCell<DeltaAccumulator>>,
    pub terminal_mode: TerminalMode,
    pub show_streaming_metrics: bool,
    /// Track last rendered content for append-only streaming pattern
    pub last_rendered_content: &'a RefCell<std::collections::HashMap<String, String>>,
}
