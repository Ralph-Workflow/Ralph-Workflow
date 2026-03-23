// json_parser/terminal/io.rs — boundary module for terminal environment I/O.
// File stem is `io` — recognized as boundary module by forbid_io_effects lint.

/// Real environment implementation for terminal detection.
///
/// Reads environment variables and checks stdout TTY status via OS calls.
struct RealTerminalEnvironment;

impl ColorEnvironment for RealTerminalEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn is_terminal(&self) -> bool {
        std::io::IsTerminal::is_terminal(&std::io::stdout())
    }
}
