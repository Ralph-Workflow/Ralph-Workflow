use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap};

use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;

pub struct ParserState {
    pub terminal_mode: RefCell<TerminalMode>,
    pub streaming_session: std::rc::Rc<RefCell<StreamingSession>>,
    pub thinking_active_index: RefCell<Option<u64>>,
    pub thinking_non_tty_indices: RefCell<BTreeSet<u64>>,
    pub suppress_thinking_for_message: RefCell<bool>,
    pub text_line_active: RefCell<bool>,
    pub cursor_up_active: RefCell<bool>,
    pub last_rendered_content: RefCell<HashMap<String, String>>,
}

impl ParserState {
    pub fn new(verbose_warnings: bool) -> Self {
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);
        Self {
            terminal_mode: RefCell::new(TerminalMode::detect()),
            streaming_session: std::rc::Rc::new(RefCell::new(streaming_session)),
            thinking_active_index: RefCell::new(None),
            thinking_non_tty_indices: RefCell::new(BTreeSet::new()),
            suppress_thinking_for_message: RefCell::new(false),
            text_line_active: RefCell::new(false),
            cursor_up_active: RefCell::new(false),
            last_rendered_content: RefCell::new(HashMap::new()),
        }
    }

    pub fn with_session_mut<R>(&self, f: impl FnOnce(&mut StreamingSession) -> R) -> R {
        f(&mut self.streaming_session.borrow_mut())
    }

    pub fn with_cursor_up_active_mut<R>(&self, f: impl FnOnce(&mut bool) -> R) -> R {
        f(&mut self.cursor_up_active.borrow_mut())
    }

    pub fn with_thinking_active_index_mut<R>(&self, f: impl FnOnce(&mut Option<u64>) -> R) -> R {
        f(&mut self.thinking_active_index.borrow_mut())
    }

    pub fn with_thinking_non_tty_indices_mut<R>(
        &self,
        f: impl FnOnce(&mut BTreeSet<u64>) -> R,
    ) -> R {
        f(&mut self.thinking_non_tty_indices.borrow_mut())
    }

    pub fn with_suppress_thinking_for_message_mut<R>(&self, f: impl FnOnce(&mut bool) -> R) -> R {
        f(&mut self.suppress_thinking_for_message.borrow_mut())
    }

    pub fn with_text_line_active_mut<R>(&self, f: impl FnOnce(&mut bool) -> R) -> R {
        f(&mut self.text_line_active.borrow_mut())
    }

    pub fn with_last_rendered_content_mut<R>(
        &self,
        f: impl FnOnce(&mut HashMap<String, String>) -> R,
    ) -> R {
        f(&mut self.last_rendered_content.borrow_mut())
    }

    pub fn with_terminal_mode_mut<R>(&self, f: impl FnOnce(&mut TerminalMode) -> R) -> R {
        f(&mut self.terminal_mode.borrow_mut())
    }
}
