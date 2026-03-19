use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;

use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;

pub struct OpenCodeParserState {
    pub streaming_session: Rc<RefCell<StreamingSession>>,
    pub terminal_mode: RefCell<TerminalMode>,
    pub last_rendered_content: RefCell<HashMap<String, String>>,
    pub fallback_step_counter: Cell<u64>,
}

impl OpenCodeParserState {
    pub fn new(verbose_warnings: bool) -> Self {
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);
        Self {
            streaming_session: Rc::new(RefCell::new(streaming_session)),
            terminal_mode: RefCell::new(TerminalMode::detect()),
            last_rendered_content: RefCell::new(HashMap::new()),
            fallback_step_counter: Cell::new(0),
        }
    }

    pub fn with_session_mut<R>(&self, f: impl FnOnce(&mut StreamingSession) -> R) -> R {
        f(&mut self.streaming_session.borrow_mut())
    }

    pub fn with_last_rendered_content_mut<R>(
        &self,
        f: impl FnOnce(&mut HashMap<String, String>) -> R,
    ) -> R {
        f(&mut self.last_rendered_content.borrow_mut())
    }

    pub fn next_fallback_step_id(&self, session: &str, timestamp: Option<u64>) -> String {
        let counter = self.fallback_step_counter.get().saturating_add(1);
        self.fallback_step_counter.set(counter);
        timestamp.map_or_else(
            || format!("{session}:fallback:{counter}"),
            |ts| format!("{session}:{ts}:{counter}"),
        )
    }
}
