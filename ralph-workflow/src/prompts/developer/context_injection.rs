// Context injection for prompts.
//
// Contains constants and helper functions for injecting context into prompts,
// including XSD schema files and retry context files.

/// The XSD schema for development result validation - included at compile time
const DEVELOPMENT_RESULT_XSD_SCHEMA: &str =
    include_str!("../../files/llm_output_extraction/development_result.xsd");
const DEVELOPMENT_CONTINUATION_RESULT_XSD_SCHEMA: &str =
    include_str!("../../files/llm_output_extraction/development_continuation_result.xsd");

/// The XSD schema for plan validation - included at compile time
const PLAN_XSD_SCHEMA: &str = include_str!("../../files/llm_output_extraction/plan.xsd");

/// Directory for XSD retry context files
const XSD_RETRY_TMP_DIR: &str = ".agent/tmp";

/// Write just the XSD schema file to `.agent/tmp/` directory.
///
/// This is called before the initial planning prompt so the agent can reference
/// the schema if needed. The schema provides the authoritative definition of
/// valid XML structure.
fn write_planning_xsd_schema_file(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }

    let _ = workspace.write(&tmp_dir.join("plan.xsd"), PLAN_XSD_SCHEMA);
}

fn write_planning_xsd_retry_schema_files(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }
    let _ = workspace.write(&tmp_dir.join("plan.xsd"), PLAN_XSD_SCHEMA);
}

/// Write XSD retry context files to `.agent/tmp/` directory.
///
/// This writes the XSD schema and last output to files so they don't bloat the prompt.
/// The agent MUST read these files to understand what went wrong and fix it.
fn write_planning_xsd_retry_files(workspace: &dyn Workspace, last_output: &str) {
    write_planning_xsd_retry_schema_files(workspace);
    let _ = workspace.write(
        &Path::new(XSD_RETRY_TMP_DIR).join("last_output.xml"),
        last_output,
    );
}

/// Write XSD retry context files for development iteration to `.agent/tmp/` directory.
fn write_dev_iteration_xsd_retry_schema_files(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }

    let _ = workspace.write(
        &tmp_dir.join("development_result.xsd"),
        DEVELOPMENT_RESULT_XSD_SCHEMA,
    );
    let _ = workspace.write(
        &tmp_dir.join("development_continuation_result.xsd"),
        DEVELOPMENT_CONTINUATION_RESULT_XSD_SCHEMA,
    );
}

fn write_dev_iteration_continuation_schema_file(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }

    let _ = workspace.write(
        &tmp_dir.join("development_continuation_result.xsd"),
        DEVELOPMENT_CONTINUATION_RESULT_XSD_SCHEMA,
    );
}

/// Write XSD retry context files for development iteration to `.agent/tmp/` directory.
fn write_dev_iteration_xsd_retry_files(workspace: &dyn Workspace, last_output: &str) {
    write_dev_iteration_xsd_retry_schema_files(workspace);
    let _ = workspace.write(
        &Path::new(XSD_RETRY_TMP_DIR).join("last_output.xml"),
        last_output,
    );
}
