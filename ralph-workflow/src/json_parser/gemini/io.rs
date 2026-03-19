use std::cell::RefCell;
use std::collections::HashMap;

use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;

pub struct GeminiParserState {
    pub streaming_session: std::rc::Rc<RefCell<StreamingSession>>,
    pub terminal_mode: RefCell<TerminalMode>,
    pub last_rendered_content: RefCell<HashMap<String, String>>,
}

impl GeminiParserState {
    pub fn new(verbose_warnings: bool) -> Self {
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);
        Self {
            streaming_session: std::rc::Rc::new(RefCell::new(streaming_session)),
            terminal_mode: RefCell::new(TerminalMode::detect()),
            last_rendered_content: RefCell::new(HashMap::new()),
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
}
