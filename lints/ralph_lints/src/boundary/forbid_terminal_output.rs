//! Lint: `FORBID_TERMINAL_OUTPUT`
//!
//! Rejects usage of terminal output macros (`println!`, `print!`, `eprintln!`,
//! `eprint!`) outside of boundary modules.
//!
//! ## FP principle: effects belong at the boundary
//!
//! In Haskell, terminal output is an `IO` action — it cannot occur in pure
//! code. Pure functions should return values; effectful functions that emit
//! output should live in boundary modules.
//!
//! ## Boundary exceptions
//!
//! Boundary code (CLI entrypoints, runtime logging, interactive prompts)
//! legitimately needs terminal output.

use crate::domain::boundary::is_in_boundary_module;
use rustc_ast::ast::{Expr, ExprKind, MacCall};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects terminal output macros outside boundary modules.
    ///
    /// ### Example (bad — output in domain logic)
    ///
    /// ```rust,ignore
    /// fn process_item(item: &Item) -> Result {
    ///     println!("Processing: {}", item.name);  // effect in pure code
    ///     // ...
    /// }
    /// ```
    ///
    /// ### Example (good — output in boundary module)
    ///
    /// ```rust,ignore
    /// // boundary/cli.rs
    /// fn run_process(item: &Item) -> Result {
    ///     println!("Processing: {}", item.name);  // OK in boundary
    ///     process_item(item)
    /// }
    /// ```
    pub FORBID_TERMINAL_OUTPUT,
    Warn,
    "terminal output macros (`println!`, `print!`, etc.) are forbidden outside boundary modules"
}

const TERMINAL_OUTPUT_MACROS: &[&str] = &["println", "print", "eprintln", "eprint"];

declare_lint_pass!(ForbidTerminalOutput => [FORBID_TERMINAL_OUTPUT]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_TERMINAL_OUTPUT]);
    lint_store.register_early_pass(|| Box::new(ForbidTerminalOutput));
}

fn terminal_macro_name(mac: &MacCall) -> Option<&'static str> {
    mac.path.segments.last().and_then(|seg| {
        let name = seg.ident.name.as_str();
        TERMINAL_OUTPUT_MACROS.iter().find(|&&m| m == name).copied()
    })
}

impl EarlyLintPass for ForbidTerminalOutput {
    fn check_expr(&mut self, cx: &EarlyContext<'_>, expr: &Expr) {
        let ExprKind::MacCall(mac) = &expr.kind else {
            return;
        };

        let Some(macro_name) = terminal_macro_name(mac) else {
            return;
        };

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        cx.span_lint(FORBID_TERMINAL_OUTPUT, expr.span, |diag| {
            diag.primary_message(format!(
                "`{macro_name}!` is forbidden outside boundary modules"
            ));
            diag.help(
                "return values or diagnostics from pure functions; emit output only at \
                 boundaries. For logging, return structured data and let boundary code \
                 format/emit it. See `docs/code-style/boundaries.md`.",
            );
            diag.note(
                "if terminal output is genuinely required for CLI or runtime interaction, \
                 move this code into a boundary module (io/, runtime/, ffi/, boundary/). \
                 Style guide: `docs/code-style/boundaries.md`.",
            );
        });
    }
}

#[cfg(test)]
mod tests {
    use super::TERMINAL_OUTPUT_MACROS;

    #[test]
    fn all_print_macros_are_covered() {
        assert!(TERMINAL_OUTPUT_MACROS.contains(&"println"));
        assert!(TERMINAL_OUTPUT_MACROS.contains(&"print"));
        assert!(TERMINAL_OUTPUT_MACROS.contains(&"eprintln"));
        assert!(TERMINAL_OUTPUT_MACROS.contains(&"eprint"));
    }

    #[test]
    fn dbg_is_not_a_terminal_macro() {
        assert!(!TERMINAL_OUTPUT_MACROS.contains(&"dbg"));
    }
}
