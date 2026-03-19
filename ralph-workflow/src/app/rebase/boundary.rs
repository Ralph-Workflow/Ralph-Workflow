use std::cell::RefCell;
use std::collections::HashMap;

pub type PromptHistoryCell = RefCell<HashMap<String, crate::prompts::PromptHistoryEntry>>;

pub fn create_prompt_history_cell() -> PromptHistoryCell {
    RefCell::new(HashMap::new())
}
