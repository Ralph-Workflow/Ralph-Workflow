use serde::{Deserialize, Serialize};
use specta::Type;

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ConfigFieldSchema {
    pub name: String,
    pub label: String,
    pub description: String,
    pub field_type: String,
    pub default_value: String,
    pub min_value: Option<f64>,
    pub max_value: Option<f64>,
    pub enum_options: Vec<String>,
    pub section: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Type)]
pub struct ConfigSection {
    pub name: String,
    pub label: String,
    pub description: String,
    pub fields: Vec<ConfigFieldSchema>,
}

struct FieldSpec<'a> {
    name: &'a str,
    label: &'a str,
    description: &'a str,
    field_type: &'a str,
    default_value: &'a str,
    min_value: Option<f64>,
    max_value: Option<f64>,
    enum_options: Vec<String>,
    section: &'a str,
}

impl FieldSpec<'_> {
    fn build(self) -> ConfigFieldSchema {
        ConfigFieldSchema {
            name: self.name.to_string(),
            label: self.label.to_string(),
            description: self.description.to_string(),
            field_type: self.field_type.to_string(),
            default_value: self.default_value.to_string(),
            min_value: self.min_value,
            max_value: self.max_value,
            enum_options: self.enum_options,
            section: self.section.to_string(),
        }
    }
}

fn num(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
    min: f64,
    max: f64,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "number",
        default_value,
        min_value: Some(min),
        max_value: Some(max),
        enum_options: vec![],
        section,
    }
    .build()
}

fn bool_field(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "boolean",
        default_value,
        min_value: None,
        max_value: None,
        enum_options: vec![],
        section,
    }
    .build()
}

fn str_field(section: &str, name: &str, label: &str, description: &str) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "string",
        default_value: "",
        min_value: None,
        max_value: None,
        enum_options: vec![],
        section,
    }
    .build()
}

fn path_field(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "path",
        default_value,
        min_value: None,
        max_value: None,
        enum_options: vec![],
        section,
    }
    .build()
}

fn enum_field(
    section: &str,
    name: &str,
    label: &str,
    description: &str,
    default_value: &str,
    options: Vec<&str>,
) -> ConfigFieldSchema {
    FieldSpec {
        name,
        label,
        description,
        field_type: "enum",
        default_value,
        min_value: None,
        max_value: None,
        enum_options: options.into_iter().map(str::to_owned).collect(),
        section,
    }
    .build()
}

fn general_section() -> ConfigSection {
    let s = "general";
    ConfigSection {
        name: s.to_string(),
        label: "General".to_string(),
        description: "Core workflow settings".to_string(),
        fields: vec![
            num(
                s,
                "verbosity",
                "Verbosity",
                "Log verbosity level (0 = silent, 4 = trace)",
                "1",
                0.0,
                4.0,
            ),
            num(
                s,
                "developer_iters",
                "Developer Iterations",
                "Maximum developer iterations per run",
                "3",
                1.0,
                20.0,
            ),
            num(
                s,
                "reviewer_reviews",
                "Reviewer Passes",
                "Number of reviewer passes per iteration",
                "1",
                0.0,
                10.0,
            ),
            num(
                s,
                "max_dev_continuations",
                "Max Dev Continuations",
                "Maximum continuation attempts for the developer agent",
                "3",
                1.0,
                10.0,
            ),
            enum_field(
                s,
                "review_depth",
                "Review Depth",
                "How thorough the reviewer should be",
                "standard",
                vec!["light", "standard", "thorough"],
            ),
            path_field(
                s,
                "prompt_path",
                "Default Prompt Path",
                "Path to the default PROMPT.md file",
                "",
            ),
            path_field(
                s,
                "templates_dir",
                "Templates Directory",
                "Directory containing prompt templates",
                "~/.ralph/templates",
            ),
        ],
    }
}

fn execution_section() -> ConfigSection {
    let s = "execution";
    ConfigSection {
        name: s.to_string(),
        label: "Execution".to_string(),
        description: "How the workflow executes agent tasks".to_string(),
        fields: vec![
            bool_field(
                s,
                "checkpoint_enabled",
                "Enable Checkpointing",
                "Save progress checkpoints to allow resuming interrupted runs",
                "true",
            ),
            bool_field(
                s,
                "isolation_mode",
                "Isolation Mode",
                "Run agents in an isolated environment",
                "false",
            ),
            bool_field(
                s,
                "interactive",
                "Interactive Mode",
                "Allow interactive prompts during execution",
                "false",
            ),
            bool_field(
                s,
                "force_universal_prompt",
                "Force Universal Prompt",
                "Use a single prompt for all agents regardless of individual settings",
                "false",
            ),
            bool_field(
                s,
                "auto_detect_stack",
                "Auto-Detect Stack",
                "Automatically detect the project technology stack",
                "true",
            ),
            str_field(
                s,
                "developer_context",
                "Developer Context",
                "Additional context provided to the developer agent",
            ),
            str_field(
                s,
                "reviewer_context",
                "Reviewer Context",
                "Additional context provided to the reviewer agent",
            ),
        ],
    }
}

fn retry_section() -> ConfigSection {
    let s = "retry";
    ConfigSection {
        name: s.to_string(),
        label: "Retry and Fallback".to_string(),
        description: "How the workflow handles failures and retries".to_string(),
        fields: vec![
            num(
                s,
                "max_retries",
                "Max Retries",
                "Maximum number of retry attempts on failure",
                "3",
                0.0,
                20.0,
            ),
            num(
                s,
                "max_same_agent_retries",
                "Max Same-Agent Retries",
                "Maximum retries with the same agent before switching",
                "2",
                0.0,
                10.0,
            ),
            num(
                s,
                "retry_delay_ms",
                "Retry Delay (ms)",
                "Milliseconds to wait before each retry attempt",
                "1000",
                0.0,
                60_000.0,
            ),
            num(
                s,
                "backoff_multiplier",
                "Backoff Multiplier",
                "Exponential backoff multiplier between retries",
                "2.0",
                1.0,
                10.0,
            ),
            num(
                s,
                "max_backoff_ms",
                "Max Backoff (ms)",
                "Maximum milliseconds between retry attempts",
                "30000",
                1_000.0,
                300_000.0,
            ),
            num(
                s,
                "max_fallback_cycles",
                "Max Fallback Cycles",
                "Maximum number of fallback agent cycles",
                "2",
                0.0,
                10.0,
            ),
        ],
    }
}

fn git_section() -> ConfigSection {
    let s = "git";
    ConfigSection {
        name: s.to_string(),
        label: "Git".to_string(),
        description: "Git identity and commit settings".to_string(),
        fields: vec![
            str_field(
                s,
                "git_user_name",
                "Git User Name",
                "Name to use for automated git commits",
            ),
            str_field(
                s,
                "git_user_email",
                "Git User Email",
                "Email to use for automated git commits",
            ),
        ],
    }
}

pub fn get_config_schema() -> Result<Vec<ConfigSection>, String> {
    Ok(vec![
        general_section(),
        execution_section(),
        retry_section(),
        git_section(),
    ])
}
