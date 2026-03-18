//! Lint: `FORBID_MUT_BINDING`
//!
//! Rejects mutable LOCAL bindings (`let mut`) outside of boundary modules.
//!
//! ## FP principle: immutability by default
//!
//! In Haskell every binding is immutable — there is no `let mut`. Values are
//! transformed by producing new values, not by mutating existing ones. This
//! makes code referentially transparent.
//!
//! ## Why `mut self` parameters are allowed
//!
//! This lint allows `fn with_x(mut self, ...) -> Self` because:
//! - The mutation is internal to the function (not visible to caller)
//! - The function consumes and returns a value (pure transformation from caller's view)
//! - This is the standard Rust consuming builder pattern
//! - It's functionally equivalent to `Self { field: x, ..self }` but more efficient
//!
//! The caller never writes `mut` and sees only immutable value transformations.
//!
//! ## Boundary exceptions
//!
//! Boundary code (I/O, FFI, runtime glue) may use mutation where the
//! underlying API demands it.

use crate::domain::boundary::is_in_boundary_module;
use rustc_ast::ast::{BindingMode, Local, Mutability, PatKind};
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects mutable local bindings (`let mut`) outside of explicitly
    /// whitelisted boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`, `executor/`).
    ///
    /// Does NOT reject `mut` on function parameters (like `fn f(mut self)`) because
    /// those are internal optimizations not visible to the caller.
    ///
    /// ### Example (bad — mutable accumulator)
    ///
    /// ```rust,ignore
    /// let mut total = 0;
    /// for item in items {
    ///     total += item.price;
    /// }
    /// ```
    ///
    /// ### Example (good — immutable fold)
    ///
    /// ```rust,ignore
    /// let total: u64 = items.iter().map(|item| item.price).sum();
    /// ```
    ///
    /// ### Example (allowed — consuming builder)
    ///
    /// ```rust,ignore
    /// pub fn with_x(mut self, x: i32) -> Self {
    ///     self.field = x;
    ///     self
    /// }
    /// ```
    pub FORBID_MUT_BINDING,
    Warn,
    "`let mut` bindings are forbidden outside boundary modules"
}

declare_lint_pass!(ForbidMutBinding => [FORBID_MUT_BINDING]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_MUT_BINDING]);
    lint_store.register_early_pass(|| Box::new(ForbidMutBinding));
}

impl EarlyLintPass for ForbidMutBinding {
    // Use check_local instead of check_pat to only catch local bindings,
    // not function parameters. This allows the consuming builder pattern
    // `fn with_x(mut self) -> Self` which is functionally pure from the
    // caller's perspective.
    fn check_local(&mut self, cx: &EarlyContext<'_>, local: &Local) {
        let PatKind::Ident(BindingMode(_, Mutability::Mut), ident, _) = &local.pat.kind else {
            return;
        };

        if is_in_boundary_module(cx, local.span) {
            return;
        }

        cx.span_lint(FORBID_MUT_BINDING, local.span, |diag| {
            diag.primary_message(format!(
                "`let mut {}` is forbidden outside boundary modules",
                ident.name
            ));
            diag.help(
                "replace mutable accumulator with iterator pipeline: \
                 `xs.iter().map(f).collect()` or `xs.iter().fold(init, f)`. \
                 See quick reference in `docs/code-style/functional-transformations.md`.",
            );
            diag.note(
                "if mutation is genuinely required for I/O or FFI, move this code into a \
                 boundary module (io/, runtime/, ffi/, boundary/, executor/). Style guides: \
                 `docs/code-style/functional-transformations.md` and \
                 `docs/code-style/boundaries.md`.",
            );
        });
    }
}
