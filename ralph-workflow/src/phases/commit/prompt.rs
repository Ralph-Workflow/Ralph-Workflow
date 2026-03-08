fn build_commit_prompt(
    template_context: &TemplateContext,
    working_diff: &str,
    workspace: &dyn Workspace,
) -> (String, crate::prompts::SubstitutionLog) {
    let rendered = crate::prompts::prompt_generate_commit_message_with_diff_with_log(
        template_context,
        working_diff,
        workspace,
        "commit_message_xml",
    );
    (rendered.content, rendered.log)
}

fn stderr_contains_auth_error(stderr: &str) -> bool {
    let lower = stderr.to_lowercase();
    lower.contains("authentication")
        || lower.contains("api key")
        || lower.contains("invalid key")
        || lower.contains("unauthorized")
        || lower.contains("permission denied")
}
