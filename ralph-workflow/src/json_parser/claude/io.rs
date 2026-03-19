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
}
