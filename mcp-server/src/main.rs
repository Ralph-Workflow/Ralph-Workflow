use mcp_server::dispatch::access::{AccessDecision, AccessMode, McpCapability};
use mcp_server::dispatch::{
    DirEntry, HostSession, ToolHandler, ToolMetadata, ToolRegistry, WorkspaceAdapter,
};
use mcp_server::io::{McpServer, ServerState, StdioTransport};
use mcp_server::protocol::ToolDefinition;
use serde_json::json;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

struct CliOptions {
    root_dir: PathBuf,
    read_only: bool,
    session_id: String,
    run_id: String,
}

struct StandaloneSession {
    session_id: String,
    run_id: String,
}

impl HostSession for StandaloneSession {
    fn session_id(&self) -> &str {
        &self.session_id
    }

    fn run_id(&self) -> &str {
        &self.run_id
    }

    fn check_capability(&self, _cap: McpCapability) -> AccessDecision {
        AccessDecision::Allow
    }
}

struct FsWorkspace {
    root: PathBuf,
}

impl FsWorkspace {
    fn resolve(&self, path: &Path) -> PathBuf {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.root.join(path)
        }
    }
}

impl WorkspaceAdapter for FsWorkspace {
    fn read(&self, path: &Path) -> Result<String, String> {
        let resolved = self.resolve(path);
        fs::read_to_string(&resolved)
            .map_err(|e| format!("failed to read '{}': {}", resolved.display(), e))
    }

    fn write(&self, path: &Path, content: &str) -> Result<(), String> {
        let resolved = self.resolve(path);
        if let Some(parent) = resolved.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("failed to create dir '{}': {}", parent.display(), e))?;
        }
        fs::write(&resolved, content)
            .map_err(|e| format!("failed to write '{}': {}", resolved.display(), e))
    }

    fn exists(&self, path: &Path) -> bool {
        self.resolve(path).exists()
    }

    fn read_dir(&self, path: &Path) -> Result<Vec<DirEntry>, String> {
        let resolved = self.resolve(path);
        let entries = fs::read_dir(&resolved)
            .map_err(|e| format!("failed to read dir '{}': {}", resolved.display(), e))?;

        entries
            .map(|entry| {
                entry
                    .map_err(|e| {
                        format!(
                            "failed to read dir entry in '{}': {}",
                            resolved.display(),
                            e
                        )
                    })
                    .and_then(|e| {
                        let entry_path = e.path();
                        e.file_type()
                            .map_err(|err| {
                                format!(
                                    "failed to inspect file type for '{}': {}",
                                    entry_path.display(),
                                    err
                                )
                            })
                            .map(|file_type| DirEntry {
                                path: entry_path.display().to_string(),
                                is_dir: file_type.is_dir(),
                            })
                    })
            })
            .collect()
    }
}

fn usage() -> &'static str {
    "mcp-server - standalone MCP server\n\nUsage:\n  mcp-server [--root-dir <path>] [--read-only] [--session-id <id>] [--run-id <id>]\n  mcp-server --help\n\nDefaults:\n  --root-dir    current working directory\n  --session-id  standalone-session\n  --run-id      standalone-run\n"
}

fn parse_args(args: &[String]) -> Result<Option<CliOptions>, String> {
    let defaults = default_cli_options()?;
    parse_args_with_defaults(args, defaults)
}

fn parse_args_with_defaults(
    args: &[String],
    options: CliOptions,
) -> Result<Option<CliOptions>, String> {
    let mut cursor = ArgCursor::new(args);
    parse_flags_recursively(&mut cursor, options)
}

fn parse_flags_recursively(
    cursor: &mut ArgCursor<'_>,
    options: CliOptions,
) -> Result<Option<CliOptions>, String> {
    match cursor.next_flag() {
        None => Ok(Some(options)),
        Some(flag) => parse_flag_and_continue(flag, cursor, options),
    }
}

fn parse_flag_and_continue(
    flag: &str,
    cursor: &mut ArgCursor<'_>,
    options: CliOptions,
) -> Result<Option<CliOptions>, String> {
    let parsed = parse_flag(flag, cursor)?;
    if matches!(parsed, ParsedFlag::Help) {
        return Ok(None);
    }
    let mut next = options;
    apply_parsed_flag(&mut next, parsed);
    parse_flags_recursively(cursor, next)
}

fn default_cli_options() -> Result<CliOptions, String> {
    let root_dir =
        env::current_dir().map_err(|e| format!("failed to read current directory: {e}"))?;
    Ok(CliOptions {
        root_dir,
        read_only: false,
        session_id: String::from("standalone-session"),
        run_id: String::from("standalone-run"),
    })
}

struct ArgCursor<'a> {
    args: &'a [String],
    index: usize,
}

impl<'a> ArgCursor<'a> {
    fn new(args: &'a [String]) -> Self {
        Self { args, index: 1 }
    }

    fn next_flag(&mut self) -> Option<&'a str> {
        let flag = self.args.get(self.index).map(std::string::String::as_str);
        if flag.is_some() {
            self.index += 1;
        }
        flag
    }

    fn next_value(&mut self, flag: &str) -> Result<String, String> {
        let value = self
            .args
            .get(self.index)
            .cloned()
            .ok_or_else(|| format!("{flag} requires a value"))?;
        self.index += 1;
        Ok(value)
    }
}

enum ParsedFlag {
    Help,
    ReadOnly,
    RootDir(String),
    SessionId(String),
    RunId(String),
}

fn parse_flag(flag: &str, cursor: &mut ArgCursor<'_>) -> Result<ParsedFlag, String> {
    match flag {
        "--help" | "-h" => Ok(ParsedFlag::Help),
        "--read-only" => Ok(ParsedFlag::ReadOnly),
        "--root-dir" => cursor.next_value("--root-dir").map(ParsedFlag::RootDir),
        "--session-id" => cursor.next_value("--session-id").map(ParsedFlag::SessionId),
        "--run-id" => cursor.next_value("--run-id").map(ParsedFlag::RunId),
        unknown => Err(format!("unknown argument: {unknown}")),
    }
}

fn apply_parsed_flag(options: &mut CliOptions, parsed: ParsedFlag) {
    match parsed {
        ParsedFlag::Help => {}
        ParsedFlag::ReadOnly => options.read_only = true,
        ParsedFlag::RootDir(path) => options.root_dir = PathBuf::from(path),
        ParsedFlag::SessionId(value) => options.session_id = value,
        ParsedFlag::RunId(value) => options.run_id = value,
    }
}

fn build_default_registry() -> ToolRegistry {
    let read_definition = ToolDefinition {
        name: "read_file".to_string(),
        description: "Read a file from the configured root directory".to_string(),
        input_schema: json!({
            "type": "object",
            "properties": { "path": { "type": "string" } },
            "required": ["path"]
        }),
    };
    let write_definition = ToolDefinition {
        name: "write_file".to_string(),
        description: "Write a file under the configured root directory".to_string(),
        input_schema: json!({
            "type": "object",
            "properties": {
                "path": { "type": "string" },
                "content": { "type": "string" }
            },
            "required": ["path", "content"]
        }),
    };

    let read_handler: ToolHandler = Arc::new(mcp_server::dispatch::handle_read_file);
    let write_handler: ToolHandler = Arc::new(mcp_server::dispatch::handle_write_file);

    ToolRegistry::new(vec![
        (
            ToolMetadata {
                definition: read_definition,
                required_capability: McpCapability::WorkspaceRead,
                is_mutating: Some(false),
            },
            read_handler,
        ),
        (
            ToolMetadata {
                definition: write_definition,
                required_capability: McpCapability::WorkspaceWriteTracked,
                is_mutating: Some(true),
            },
            write_handler,
        ),
    ])
}

fn run_stdio_server(options: CliOptions) -> Result<(), String> {
    let server = build_server_for_options(options);
    run_server_loop(server)
}

fn build_server_for_options(options: CliOptions) -> McpServer {
    let session = Arc::new(StandaloneSession {
        session_id: options.session_id.clone(),
        run_id: options.run_id.clone(),
    }) as Arc<dyn HostSession>;
    let workspace = Arc::new(FsWorkspace {
        root: options.root_dir.clone(),
    }) as Arc<dyn WorkspaceAdapter>;
    let config = apply_access_mode(
        mcp_server::io::McpServerConfig::new(options.root_dir)
            .with_session_id(options.session_id)
            .with_run_id(options.run_id),
        options.read_only,
    );
    let registry = build_default_registry();
    McpServer::new(session, config, workspace, registry, None)
}

fn apply_access_mode(
    config: mcp_server::io::McpServerConfig,
    read_only: bool,
) -> mcp_server::io::McpServerConfig {
    if read_only {
        config.with_access_mode(AccessMode::ReadOnly)
    } else {
        config
    }
}

fn run_server_loop(server: McpServer) -> Result<(), String> {
    let mut stream = StdioTransport::with_default_stdio();
    let mut state = ServerState::Uninitialized;

    loop {
        let maybe_request = mcp_server::io::McpStream::read_request(&mut stream)
            .map_err(|e| format!("transport read error: {e}"))?;
        let Some(request) = maybe_request else {
            return Ok(());
        };

        let (response, next_state) = server.handle_request(request, state);
        state = next_state;

        write_optional_response(&mut stream, response)?;
        if state == ServerState::Shutdown {
            return Ok(());
        }
    }
}

fn write_optional_response(
    stream: &mut StdioTransport,
    response: Option<mcp_server::protocol::JsonRpcResponse>,
) -> Result<(), String> {
    if let Some(payload) = response {
        mcp_server::io::McpStream::write_response(stream, &payload)
            .map_err(|e| format!("transport write error: {e}"))?;
    }
    Ok(())
}

fn main() {
    std::process::exit(main_exit_code())
}

fn main_exit_code() -> i32 {
    let args: Vec<String> = env::args().collect();
    let outcome = run_main(args);
    emit_main_outcome(&outcome);
    outcome.exit_code
}

struct MainOutcome {
    exit_code: i32,
    stdout: Option<String>,
    stderr: Option<String>,
}

fn run_main(args: Vec<String>) -> MainOutcome {
    match parse_args(&args) {
        Ok(None) => MainOutcome {
            exit_code: 0,
            stdout: Some(usage().to_string()),
            stderr: None,
        },
        Ok(Some(options)) => run_server_outcome(options),
        Err(err) => MainOutcome {
            exit_code: 2,
            stdout: None,
            stderr: Some(format!("{err}\n\n{}", usage())),
        },
    }
}

fn run_server_outcome(options: CliOptions) -> MainOutcome {
    match run_stdio_server(options) {
        Ok(()) => MainOutcome {
            exit_code: 0,
            stdout: None,
            stderr: None,
        },
        Err(err) => MainOutcome {
            exit_code: 1,
            stdout: None,
            stderr: Some(format!("mcp-server failed: {err}")),
        },
    }
}

fn emit_main_outcome(outcome: &MainOutcome) {
    if let Some(stdout) = outcome.stdout.as_ref() {
        println!("{}", stdout);
    }
    if let Some(stderr) = outcome.stderr.as_ref() {
        eprintln!("{}", stderr);
    }
}
