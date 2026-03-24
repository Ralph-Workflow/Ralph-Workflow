use std::cell::RefCell;
use std::collections::HashMap;

pub(super) type PromptHistoryCell = RefCell<HashMap<String, crate::prompts::PromptHistoryEntry>>;

pub(super) fn create_prompt_history_cell() -> PromptHistoryCell {
    RefCell::new(HashMap::new())
}
