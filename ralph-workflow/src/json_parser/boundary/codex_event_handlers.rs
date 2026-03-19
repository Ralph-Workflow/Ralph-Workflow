use crate::json_parser::streaming_state::StreamingSession;
use crate::json_parser::types::DeltaAccumulator;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

pub struct EventHandlerContext<'a> {
    pub colors: &'a crate::logger::Colors,
    pub verbosity: crate::config::Verbosity,
    pub display_name: &'a str,
    pub streaming_session: &'a Rc<RefCell<StreamingSession>>,
    pub reasoning_accumulator: &'a Rc<RefCell<DeltaAccumulator>>,
    pub terminal_mode: crate::json_parser::terminal::TerminalMode,
    pub show_streaming_metrics: bool,
    pub last_rendered_content: &'a RefCell<HashMap<String, String>>,
}

pub mod item_started;
pub mod item_completed;
pub mod turn;
pub mod error;
pub mod item_dispatch;

pub use item_started::handle_agent_message_started;
pub use item_started::handle_reasoning_started;
pub use item_started::handle_tool_use_started;
pub use item_completed::handle_agent_message_completed;
pub use item_completed::handle_reasoning_completed;
pub use item_completed::handle_tool_use_completed;
pub use turn::handle_turn_started;
pub use turn::handle_turn_completed;
pub use turn::handle_turn_failed;
pub use error::handle_error;
