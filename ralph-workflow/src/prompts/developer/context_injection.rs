// Context injection for prompts.
//
// Contains constants and helper functions for injecting context into prompts,
// including XSD schema files.

/// The XSD schema for development result validation - included at compile time
const DEVELOPMENT_RESULT_XSD_SCHEMA: &str = include_str!("../xsd/development_result.xsd");

/// The XSD schema for plan validation - included at compile time
const PLAN_XSD_SCHEMA: &str = include_str!("../xsd/plan.xsd");

/// Directory for agent temporary context files
const AGENT_TMP_DIR: &str = ".agent/tmp";

/// Write just the XSD schema file to `.agent/tmp/` directory.
///
/// This is called before the initial planning prompt so the agent can reference
/// the schema if needed. The schema provides the authoritative definition of
/// valid XML structure.
fn write_planning_xsd_schema_file(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(AGENT_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }

    let _ = workspace.write(&tmp_dir.join("plan.xsd"), PLAN_XSD_SCHEMA);
}

/// Write development result XSD schema file to `.agent/tmp/` directory.
fn write_dev_iteration_xsd_schema_file(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(AGENT_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }

    let _ = workspace.write(
        &tmp_dir.join("development_result.xsd"),
        DEVELOPMENT_RESULT_XSD_SCHEMA,
    );
}
