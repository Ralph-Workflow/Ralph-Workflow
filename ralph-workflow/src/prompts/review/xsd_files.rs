// XSD schema constants and file-writing helpers for review and fix prompts.

/// The XSD schema for issues validation - included at compile time
const ISSUES_XSD_SCHEMA: &str = include_str!("../xsd/issues.xsd");

/// The XSD schema for fix result validation - included at compile time
const FIX_RESULT_XSD_SCHEMA: &str = include_str!("../xsd/fix_result.xsd");

/// Directory for XSD retry context files
const XSD_RETRY_TMP_DIR: &str = ".agent/tmp";

/// Write XSD retry context files for review to `.agent/tmp/` directory.
fn write_review_xsd_retry_schema_files(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }
    let _ = workspace.write(&tmp_dir.join("issues.xsd"), ISSUES_XSD_SCHEMA);
}

fn write_review_xsd_retry_files(workspace: &dyn Workspace, last_output: &str) {
    write_review_xsd_retry_schema_files(workspace);
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    let _ = workspace.write(&tmp_dir.join("last_output.xml"), last_output);
}

/// Write XSD retry context files for fix result to `.agent/tmp/` directory.
fn write_fix_xsd_retry_schema_files(workspace: &dyn Workspace) {
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    if workspace.create_dir_all(tmp_dir).is_err() {
        return;
    }
    let _ = workspace.write(&tmp_dir.join("fix_result.xsd"), FIX_RESULT_XSD_SCHEMA);
}

fn write_fix_xsd_retry_files(workspace: &dyn Workspace, last_output: &str) {
    write_fix_xsd_retry_schema_files(workspace);
    let tmp_dir = Path::new(XSD_RETRY_TMP_DIR);
    let _ = workspace.write(&tmp_dir.join("last_output.xml"), last_output);
}
