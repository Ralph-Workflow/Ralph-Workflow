use std::process::Command;

#[test]
fn standalone_binary_help_exits_successfully() {
    let binary_path = std::env::var("CARGO_BIN_EXE_mcp-server")
        .or_else(|_| std::env::var("CARGO_BIN_EXE_mcp_server"))
        .unwrap_or_else(|_| format!("{}/../target/debug/mcp-server", env!("CARGO_MANIFEST_DIR")));
    let output = Command::new(binary_path)
        .arg("--help")
        .output()
        .expect("failed to execute mcp-server --help");

    assert!(
        output.status.success(),
        "expected --help to exit successfully, got status={:?}, stderr={} ",
        output.status.code(),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("mcp-server") || stdout.contains("MCP"),
        "expected help output to mention mcp-server/MCP, got: {stdout}"
    );
}
