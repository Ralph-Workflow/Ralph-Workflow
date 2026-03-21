#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ItemStartedInterpretation {
    CommandExecution,
    AgentMessage,
    Reasoning,
    FileRead,
    FileWrite,
    McpTool,
    WebSearch,
    PlanUpdate,
    Unknown(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ItemCompletedInterpretation {
    AgentMessage,
    Reasoning,
    CommandExecution,
    FileWrite,
    FileRead,
    McpTool,
    WebSearch,
    PlanUpdate,
}

pub fn interpret_item_started_type(item_type: Option<&str>) -> Option<ItemStartedInterpretation> {
    item_type.map(|item_type| match item_type {
        "command_execution" => ItemStartedInterpretation::CommandExecution,
        "agent_message" => ItemStartedInterpretation::AgentMessage,
        "reasoning" => ItemStartedInterpretation::Reasoning,
        "file_read" => ItemStartedInterpretation::FileRead,
        "file_write" => ItemStartedInterpretation::FileWrite,
        "mcp_tool_call" | "mcp" => ItemStartedInterpretation::McpTool,
        "web_search" => ItemStartedInterpretation::WebSearch,
        "plan_update" => ItemStartedInterpretation::PlanUpdate,
        other => ItemStartedInterpretation::Unknown(other.to_string()),
    })
}

pub fn interpret_item_completed_type(
    item_type: Option<&str>,
) -> Option<ItemCompletedInterpretation> {
    item_type.and_then(|item_type| match item_type {
        "agent_message" => Some(ItemCompletedInterpretation::AgentMessage),
        "reasoning" => Some(ItemCompletedInterpretation::Reasoning),
        "command_execution" => Some(ItemCompletedInterpretation::CommandExecution),
        "file_change" | "file_write" => Some(ItemCompletedInterpretation::FileWrite),
        "file_read" => Some(ItemCompletedInterpretation::FileRead),
        "mcp_tool_call" | "mcp" => Some(ItemCompletedInterpretation::McpTool),
        "web_search" => Some(ItemCompletedInterpretation::WebSearch),
        "plan_update" => Some(ItemCompletedInterpretation::PlanUpdate),
        _ => None,
    })
}

pub fn compute_reasoning_incremental_delta(
    previous_content: &str,
    current_content: &str,
) -> String {
    if let Some(stripped) = current_content.strip_prefix(previous_content) {
        stripped.to_string()
    } else {
        current_content.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::{
        compute_reasoning_incremental_delta, interpret_item_completed_type,
        interpret_item_started_type, ItemCompletedInterpretation, ItemStartedInterpretation,
    };

    #[test]
    fn started_type_maps_aliases_to_mcp_tool() {
        assert_eq!(
            interpret_item_started_type(Some("mcp")),
            Some(ItemStartedInterpretation::McpTool)
        );
    }

    #[test]
    fn completed_type_maps_file_change_to_file_write() {
        assert_eq!(
            interpret_item_completed_type(Some("file_change")),
            Some(ItemCompletedInterpretation::FileWrite)
        );
    }

    #[test]
    fn reasoning_delta_returns_suffix_when_content_extends_previous() {
        assert_eq!(
            compute_reasoning_incremental_delta("hello", "hello world"),
            " world"
        );
    }

    #[test]
    fn started_type_maps_unknown_type_to_unknown_variant() {
        assert_eq!(
            interpret_item_started_type(Some("custom_event")),
            Some(ItemStartedInterpretation::Unknown(
                "custom_event".to_string()
            ))
        );
    }

    #[test]
    fn completed_type_returns_none_for_unknown_type() {
        assert_eq!(interpret_item_completed_type(Some("custom_event")), None);
    }

    #[test]
    fn reasoning_delta_returns_whole_content_when_not_prefix_extension() {
        assert_eq!(
            compute_reasoning_incremental_delta("old value", "new value"),
            "new value"
        );
    }
}
