use crate::json_parser::streaming_state::StreamingSession;
use std::cell::RefCell;
use std::rc::Rc;

#[derive(Debug, Clone)]
pub struct StreamingState {
    session: Rc<RefCell<StreamingSession>>,
    terminal_mode: Rc<RefCell<crate::json_parser::terminal::TerminalMode>>,
    thinking_active_index: Rc<RefCell<Option<u64>>>,
    thinking_non_tty_indices: Rc<RefCell<std::collections::BTreeSet<u64>>>,
    suppress_thinking_for_message: Rc<RefCell<bool>>,
    text_line_active: Rc<RefCell<bool>>,
    cursor_up_active: Rc<RefCell<bool>>,
    last_rendered_content: Rc<RefCell<std::collections::HashMap<String, String>>>,
}

impl StreamingState {
    #[must_use]
    pub fn new() -> Self {
        Self {
            session: Rc::new(RefCell::new(StreamingSession::new())),
            terminal_mode: Rc::new(RefCell::new(
                crate::json_parser::terminal::TerminalMode::detect(),
            )),
            thinking_active_index: Rc::new(RefCell::new(None)),
            thinking_non_tty_indices: Rc::new(RefCell::new(std::collections::BTreeSet::new())),
            suppress_thinking_for_message: Rc::new(RefCell::new(false)),
            text_line_active: Rc::new(RefCell::new(false)),
            cursor_up_active: Rc::new(RefCell::new(false)),
            last_rendered_content: Rc::new(RefCell::new(std::collections::HashMap::new())),
        }
    }

    pub fn borrow_session(&self) -> std::cell::Ref<'_, StreamingSession> {
        self.session.borrow()
    }

    pub fn borrow_session_mut(&self) -> std::cell::RefMut<'_, StreamingSession> {
        self.session.borrow_mut()
    }

    pub fn terminal_mode(&self) -> std::cell::Ref<'_, crate::json_parser::terminal::TerminalMode> {
        self.terminal_mode.borrow()
    }

    pub fn terminal_mode_mut(
        &self,
    ) -> std::cell::RefMut<'_, crate::json_parser::terminal::TerminalMode> {
        self.terminal_mode.borrow_mut()
    }

    pub fn thinking_active_index(&self) -> std::cell::Ref<'_, Option<u64>> {
        self.thinking_active_index.borrow()
    }

    pub fn thinking_active_index_mut(&self) -> std::cell::RefMut<'_, Option<u64>> {
        self.thinking_active_index.borrow_mut()
    }

    pub fn thinking_non_tty_indices(&self) -> std::cell::Ref<'_, std::collections::BTreeSet<u64>> {
        self.thinking_non_tty_indices.borrow()
    }

    pub fn thinking_non_tty_indices_mut(
        &self,
    ) -> std::cell::RefMut<'_, std::collections::BTreeSet<u64>> {
        self.thinking_non_tty_indices.borrow_mut()
    }

    pub fn suppress_thinking_for_message(&self) -> std::cell::Ref<'_, bool> {
        self.suppress_thinking_for_message.borrow()
    }

    pub fn suppress_thinking_for_message_mut(&self) -> std::cell::RefMut<'_, bool> {
        self.suppress_thinking_for_message.borrow_mut()
    }

    pub fn text_line_active(&self) -> std::cell::Ref<'_, bool> {
        self.text_line_active.borrow()
    }

    pub fn text_line_active_mut(&self) -> std::cell::RefMut<'_, bool> {
        self.text_line_active.borrow_mut()
    }

    pub fn cursor_up_active(&self) -> std::cell::Ref<'_, bool> {
        self.cursor_up_active.borrow()
    }

    pub fn cursor_up_active_mut(&self) -> std::cell::RefMut<'_, bool> {
        self.cursor_up_active.borrow_mut()
    }

    pub fn last_rendered_content(
        &self,
    ) -> std::cell::Ref<'_, std::collections::HashMap<String, String>> {
        self.last_rendered_content.borrow()
    }

    pub fn last_rendered_content_mut(
        &self,
    ) -> std::cell::RefMut<'_, std::collections::HashMap<String, String>> {
        self.last_rendered_content.borrow_mut()
    }
}

impl Default for StreamingState {
    fn default() -> Self {
        Self::new()
    }
}
