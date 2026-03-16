#![feature(rustc_private)]
// DO NOT CHANGE LINTING POLICY UNLESS THE USER SPECIFICALLY ASKS TO, YOU MUST REFACTOR EVEN IF IT TAKES YOU LONG TIME
#![deny(warnings)]
#![deny(clippy::all)]

extern crate rustc_ast;
extern crate rustc_span;

use rustc_ast::ast::{Expr, ExprKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_span::FileName;
use std::path::{Component, Path};

/// Boundary module path components where imperative loops are permitted.
const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

dylint_linting::impl_early_lint! {
    /// ### What it does
    ///
    /// Rejects `while`, `loop`, and `for` loop constructs outside of
    /// explicitly whitelisted boundary modules.
    ///
    /// ### Why is this bad?
    ///
    /// Imperative loops encourage mutable accumulators, index manipulation,
    /// and break/continue control flow that obscures intent. Iterator
    /// combinators (`map`, `filter`, `fold`, `for_each`, etc.) express the
    /// same transformations more declaratively and compose better.
    ///
    /// ### Boundary exceptions
    ///
    /// Low-level boundary code (I/O, runtime, FFI) sometimes needs explicit
    /// loop constructs for retry logic, polling, or byte-level parsing.
    /// Place such code in a module whose path contains one of the boundary
    /// markers.
    ///
    /// ### Example (bad)
    ///
    /// ```rust,ignore
    /// let mut result = Vec::new();
    /// for item in items {
    ///     if item.is_valid() {
    ///         result.push(item.transform());
    ///     }
    /// }
    /// ```
    ///
    /// ### Example (good)
    ///
    /// ```rust,ignore
    /// let result: Vec<_> = items
    ///     .into_iter()
    ///     .filter(|item| item.is_valid())
    ///     .map(|item| item.transform())
    ///     .collect();
    /// ```
    pub FORBID_IMPERATIVE_LOOPS,
    Deny,
    "imperative loops (`while`, `loop`, `for`) are forbidden outside boundary modules",
    ForbidImperativeLoops
}

#[derive(Default)]
pub struct ForbidImperativeLoops;

fn path_contains_boundary_component(path: &Path) -> bool {
    path.components().any(|component| match component {
        Component::Normal(name) => {
            let name_str = name.to_str().unwrap_or("");
            let stem = name_str.strip_suffix(".rs").unwrap_or(name_str);
            BOUNDARY_MODULES.iter().any(|b| *b == stem)
        }
        _ => false,
    })
}

fn is_in_boundary_module(cx: &EarlyContext<'_>, span: rustc_span::Span) -> bool {
    let source_map = cx.sess().source_map();
    let filename = source_map.span_to_filename(span);
    match &filename {
        FileName::Real(real_name) => {
            if let Some(path) = real_name.local_path() {
                path_contains_boundary_component(path)
            } else {
                false
            }
        }
        _ => false,
    }
}

impl EarlyLintPass for ForbidImperativeLoops {
    fn check_expr(&mut self, cx: &EarlyContext<'_>, expr: &Expr) {
        let loop_kind = match &expr.kind {
            ExprKind::While(_, _, _) => Some("while"),
            ExprKind::Loop(_, _, _) => Some("loop"),
            ExprKind::ForLoop { .. } => Some("for"),
            _ => None,
        };

        let Some(kind_name) = loop_kind else {
            return;
        };

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        cx.span_lint(FORBID_IMPERATIVE_LOOPS, expr.span, |diag| {
            diag.primary_message(format!(
                "`{kind_name}` loop is forbidden outside boundary modules"
            ));
            diag.help(
                "use iterator combinators (map, filter, fold, for_each, etc.) \
                 instead of imperative loops",
            );
            diag.note(
                "if a loop is genuinely required for I/O or runtime logic, move this code \
                 into a boundary module (io/, runtime/, ffi/, boundary/)",
            );
        });
    }
}

#[cfg(test)]
mod tests {
    use super::path_contains_boundary_component;
    use std::path::Path;

    #[test]
    fn boundary_io_module_is_detected() {
        assert!(path_contains_boundary_component(Path::new(
            "src/io/poller.rs"
        )));
    }

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/state.rs"
        )));
    }

    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
