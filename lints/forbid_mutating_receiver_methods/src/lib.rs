#![feature(rustc_private)]
// DO NOT CHANGE LINTING POLICY UNLESS THE USER SPECIFICALLY ASKS TO, YOU MUST REFACTOR EVEN IF IT TAKES YOU LONG TIME
#![deny(warnings)]
#![deny(clippy::all)]

extern crate rustc_hir;
extern crate rustc_middle;
extern crate rustc_span;

use rustc_hir::Expr;
use rustc_hir::ExprKind;
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_middle::ty::{self, Ty};
use rustc_span::FileName;
use std::path::{Component, Path};

/// Boundary module path components where `&mut self` method calls are permitted.
const BOUNDARY_MODULES: &[&str] = &["io", "runtime", "ffi", "boundary"];

/// Types whose `&mut self` methods are always permitted (fully qualified paths).
///
/// These are types at the outermost boundary of the application that inherently
/// require mutation (e.g., I/O handles, builders provided by frameworks).
const ALLOWED_RECEIVER_TYPES: &[&str] = &[
    // Standard I/O
    "std::io::BufWriter",
    "std::io::BufReader",
    "std::io::Cursor",
    "std::fs::File",
    "std::net::TcpStream",
    "std::net::UdpSocket",
    // Standard collections (boundary usage only)
    "std::collections::HashMap",
    "std::collections::BTreeMap",
    "std::collections::HashSet",
    "std::collections::BTreeSet",
    "std::collections::VecDeque",
    // Process / OS
    "std::process::Command",
    "std::process::Child",
    "std::process::ChildStdin",
    "std::process::ChildStdout",
    "std::process::ChildStderr",
];

dylint_linting::impl_late_lint! {
    /// ### What it does
    ///
    /// Rejects calls to methods with `&mut self` receivers unless the receiver
    /// type appears in an allowlist of boundary types, or the call site is
    /// inside a boundary module.
    ///
    /// ### Why is this bad?
    ///
    /// Calling `&mut self` methods means the caller is mutating shared state
    /// in place. In application-level code this makes data flow harder to
    /// trace and breaks referential transparency. Prefer returning new values
    /// over mutating existing ones.
    ///
    /// ### Boundary exceptions
    ///
    /// I/O handles, OS process objects, and framework builders inherently
    /// require mutation. Their types are allowlisted. Additional boundary
    /// code should live in modules marked with a boundary path component.
    ///
    /// ### Example (bad)
    ///
    /// ```rust,ignore
    /// config.set_value("key", "value"); // &mut self
    /// ```
    ///
    /// ### Example (good)
    ///
    /// ```rust,ignore
    /// let config = config.with_value("key", "value"); // returns new Config
    /// ```
    pub FORBID_MUTATING_RECEIVER_METHODS,
    Warn,
    "calls to `&mut self` methods are forbidden outside boundary modules and types",
    ForbidMutatingReceiverMethods
}

#[derive(Default)]
pub struct ForbidMutatingReceiverMethods;

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

fn is_in_boundary_module(cx: &LateContext<'_>, span: rustc_span::Span) -> bool {
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

fn is_allowed_receiver_type(ty: Ty<'_>) -> bool {
    let ty_str = format!("{ty}");

    // Strip references and smart pointers to get the underlying type
    let base = ty_str
        .strip_prefix("&mut ")
        .or_else(|| ty_str.strip_prefix("&"))
        .unwrap_or(&ty_str);

    ALLOWED_RECEIVER_TYPES
        .iter()
        .any(|allowed| base.starts_with(allowed))
}

impl<'tcx> LateLintPass<'tcx> for ForbidMutatingReceiverMethods {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        let ExprKind::MethodCall(method_name, receiver, _, _) = &expr.kind else {
            return;
        };

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        // Get the type of the receiver
        let typeck = cx.typeck_results();
        let receiver_ty = typeck.expr_ty(receiver);

        // Check if receiver type is in the allowlist
        if is_allowed_receiver_type(receiver_ty) {
            return;
        }

        // Check if the method actually takes &mut self by looking at the
        // resolved method's signature
        let Some(def_id) = typeck.type_dependent_def_id(expr.hir_id) else {
            return;
        };

        let fn_sig = cx.tcx.fn_sig(def_id).instantiate_identity();
        let inputs = fn_sig.skip_binder().inputs();
        if inputs.is_empty() {
            return;
        }

        let self_ty = inputs[0];
        let takes_mut_self = matches!(self_ty.kind(), ty::Ref(_, _, rustc_hir::Mutability::Mut));

        if !takes_mut_self {
            return;
        }

        cx.span_lint(FORBID_MUTATING_RECEIVER_METHODS, expr.span, |diag| {
            diag.primary_message(format!(
                "call to `&mut self` method `{}` is forbidden outside boundary modules",
                method_name.ident.name
            ));
            diag.help(
                "prefer methods that return a new value instead of mutating in place, \
                 or move this code into a boundary module (io/, runtime/, ffi/, boundary/)",
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
            "src/io/writer.rs"
        )));
    }

    #[test]
    fn non_boundary_module_is_not_detected() {
        assert!(!path_contains_boundary_component(Path::new(
            "src/pipeline/reducer.rs"
        )));
    }

    #[test]
    fn ui() {
        dylint_testing::ui_test(env!("CARGO_PKG_NAME"), "ui");
    }
}
