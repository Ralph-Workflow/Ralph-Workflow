use std::cell::RefCell;
use std::collections::HashMap;

use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::terminal::TerminalMode;
use crate::json_parser::types::DeltaAccumulator;

pub struct CodexParserState {
    pub streaming_session: std::rc::Rc<RefCell<StreamingSession>>,
    pub reasoning_accumulator: std::rc::Rc<RefCell<DeltaAccumulator>>,
    pub turn_counter: std::rc::Rc<RefCell<u64>>,
    pub terminal_mode: RefCell<TerminalMode>,
    pub last_rendered_content: RefCell<HashMap<String, String>>,
}

impl CodexParserState {
    pub fn new(verbose_warnings: bool) -> Self {
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);
        Self {
            streaming_session: std::rc::Rc::new(RefCell::new(streaming_session)),
            reasoning_accumulator: std::rc::Rc::new(RefCell::new(DeltaAccumulator::new())),
            turn_counter: std::rc::Rc::new(RefCell::new(0)),
            terminal_mode: RefCell::new(TerminalMode::detect()),
            last_rendered_content: RefCell::new(HashMap::new()),
        }
    }
}
