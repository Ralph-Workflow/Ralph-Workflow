use std::cell::RefCell;
use std::collections::HashMap;

pub(crate) struct ParserState {
    pub(crate) last_rendered_content: RefCell<HashMap<String, String>>,
}

impl ParserState {
    pub(crate) fn new() -> Self {
        Self {
            last_rendered_content: RefCell::new(HashMap::new()),
        }
    }
}
