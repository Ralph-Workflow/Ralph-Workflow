use crate::test_timeout::with_default_timeout;

#[test]
fn rust_analyzer_workspace_settings_use_repo_wrapper_for_clippy_and_dylint() {
    with_default_timeout(|| {
        let claude_settings = include_str!("../../.claude/settings.json");
        let settings = include_str!("../../.vscode/settings.json");
        let wrapper = include_str!("../../.cargo/rust-analyzer-dylint");

        let claude_settings_json: serde_json::Value = serde_json::from_str(claude_settings)
            .expect("Claude Code settings should be valid JSON");
        let settings_json: serde_json::Value =
            serde_json::from_str(settings).expect("workspace settings should be valid JSON");

        let override_command = settings_json
            .get("rust-analyzer.check.overrideCommand")
            .and_then(serde_json::Value::as_array)
            .expect("workspace settings should configure rust-analyzer.check.overrideCommand");

        let command_parts = override_command
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .expect("override command entries should be strings")
            })
            .collect::<Vec<_>>();

        assert!(
            command_parts == [".cargo/rust-analyzer-dylint"],
            "rust-analyzer should invoke the repo dylint wrapper"
        );
        assert!(
            wrapper.contains("cargo") && wrapper.contains("clippy"),
            "rust-analyzer wrapper should run cargo clippy"
        );
        assert!(
            wrapper.contains("cargo") && wrapper.contains("dylint"),
            "rust-analyzer wrapper should run cargo dylint"
        );
        assert!(
            wrapper.contains("cargo")
                && wrapper.contains("xtask")
                && wrapper.contains("lsp-forbidden-allow-expect"),
            "rust-analyzer wrapper should run the xtask LSP forbidden allow/expect scan"
        );
        assert!(
            wrapper.contains("--message-format=json"),
            "rust-analyzer wrapper should request JSON diagnostics"
        );
        assert!(
            wrapper.contains("line.startswith('{')"),
            "rust-analyzer wrapper should filter non-JSON output"
        );
        assert_eq!(
            claude_settings_json
                .get("env")
                .and_then(|value| value.get("RUST_ANALYZER_CHECK_OVERRIDE_COMMAND"))
                .and_then(serde_json::Value::as_str),
            Some(".cargo/rust-analyzer-dylint"),
            "Claude Code settings should expose the shared rust-analyzer wrapper path"
        );
    });
}
