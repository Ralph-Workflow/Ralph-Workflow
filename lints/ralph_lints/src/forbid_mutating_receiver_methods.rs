//! Lint: `FORBID_MUTATING_RECEIVER_METHODS`
//!
//! Rejects calls to methods with `&mut self` receivers unless the receiver
//! type is an inherently-effectful I/O type or the call site is in a boundary
//! module.
//!
//! ## FP principle: referential transparency and value semantics
//!
//! In Haskell, data structures are persistent — `Data.Map.insert` returns a
//! *new* map rather than mutating the old one. This preserves referential
//! transparency.
//!
//! ## Boundary exceptions
//!
//! I/O handles and OS process objects inherently require mutation — they
//! represent external resources, not values. Their types are allowlisted.

use crate::boundary::is_in_boundary_module;
use rustc_hir::{Expr, ExprKind, Mutability};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_middle::ty::{self, Ty};
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects calls to `&mut self` methods outside boundary modules and
    /// allowlisted I/O types.
    ///
    /// ### Example (bad — in-place mutation)
    ///
    /// ```rust,ignore
    /// config.set_value("key", "value"); // &mut self
    /// ```
    ///
    /// ### Example (good — returns new value)
    ///
    /// ```rust,ignore
    /// let config = config.with_value("key", "value"); // returns new Config
    /// ```
    pub FORBID_MUTATING_RECEIVER_METHODS,
    Deny,
    "calls to `&mut self` methods are forbidden outside boundary modules and types"
}

/// Types whose `&mut self` methods are always permitted (inherently effectful).
///
/// Standard collections are intentionally NOT in this list — domain code should
/// build collections via iterator pipelines.
const ALLOWED_RECEIVER_TYPES: &[&str] = &[
    // Standard I/O
    "std::io::BufWriter",
    "std::io::BufReader",
    "std::io::Cursor",
    "std::fs::File",
    "std::net::TcpStream",
    "std::net::UdpSocket",
    // Process / OS
    "std::process::Command",
    "std::process::Child",
    "std::process::ChildStdin",
    "std::process::ChildStdout",
    "std::process::ChildStderr",
];

declare_lint_pass!(ForbidMutatingReceiverMethods => [FORBID_MUTATING_RECEIVER_METHODS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_MUTATING_RECEIVER_METHODS]);
    lint_store.register_late_pass(|_| Box::new(ForbidMutatingReceiverMethods));
}

/// Check whether a type string matches an allowed receiver type.
fn is_allowed_receiver_str(ty_str: &str) -> bool {
    let base = ty_str
        .strip_prefix("&mut ")
        .or_else(|| ty_str.strip_prefix("&"))
        .unwrap_or(ty_str);

    ALLOWED_RECEIVER_TYPES
        .iter()
        .any(|allowed| base.starts_with(allowed))
}

/// Check whether a type is an allowed receiver type.
fn is_allowed_receiver_type(ty: Ty<'_>) -> bool {
    is_allowed_receiver_str(&format!("{ty}"))
}

impl<'tcx> LateLintPass<'tcx> for ForbidMutatingReceiverMethods {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        let ExprKind::MethodCall(method_name, receiver, _, _) = &expr.kind else {
            return;
        };

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        let typeck = cx.typeck_results();
        let receiver_ty = typeck.expr_ty(receiver);

        if is_allowed_receiver_type(receiver_ty) {
            return;
        }

        // Check if the method takes &mut self
        let Some(def_id) = typeck.type_dependent_def_id(expr.hir_id) else {
            return;
        };

        let fn_sig = cx.tcx.fn_sig(def_id).instantiate_identity();
        let inputs = fn_sig.skip_binder().inputs();

        if inputs.is_empty() {
            return;
        }

        let self_ty = inputs[0];
        let takes_mut_self = matches!(self_ty.kind(), ty::Ref(_, _, Mutability::Mut));

        if !takes_mut_self {
            return;
        }

        cx.span_lint(FORBID_MUTATING_RECEIVER_METHODS, expr.span, |diag| {
            diag.primary_message(format!(
                "call to `&mut self` method `{}` is forbidden outside boundary modules",
                method_name.ident.name
            ));
            diag.help(
                "for collections: rebuild with `.chain([item]).collect()` or use itertools \
                 (`.sorted_by_key()`, `.unique()`, `.rev()`). For structs: use builder pattern \
                 (`with_*` methods) or struct-update syntax (`..state`). \
                 See `docs/code-style/functional-transformations.md`.",
            );
        });
    }
}

#[cfg(test)]
mod tests {
    use super::is_allowed_receiver_str;

    #[test]
    fn allows_bufwriter() {
        assert!(is_allowed_receiver_str("std::io::BufWriter<std::fs::File>"));
    }

    #[test]
    fn allows_command() {
        assert!(is_allowed_receiver_str("std::process::Command"));
    }

    #[test]
    fn rejects_hashmap() {
        assert!(!is_allowed_receiver_str(
            "std::collections::HashMap<String, String>"
        ));
    }

    #[test]
    fn rejects_vec() {
        assert!(!is_allowed_receiver_str("Vec<i32>"));
    }

    #[test]
    fn rejects_custom_type() {
        assert!(!is_allowed_receiver_str("my_app::Config"));
    }
}
