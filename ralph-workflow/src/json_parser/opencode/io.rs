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
}
