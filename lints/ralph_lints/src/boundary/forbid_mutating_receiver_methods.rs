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

use crate::domain::boundary::is_in_boundary_module;
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
    Warn,
    "calls to `&mut self` methods are forbidden outside boundary modules and types"
}

const ALLOWED_RECEIVER_TYPES: &[&str] = &[
    "std::io::BufWriter",
    "std::io::BufReader",
    "std::io::Cursor",
    "std::io::Read",
    "std::io::Write",
    "std::fs::File",
    "std::net::TcpStream",
    "std::net::UdpSocket",
    "std::process::Command",
    "std::process::Child",
    "std::process::ChildStdin",
    "std::process::ChildStdout",
    "std::process::ChildStderr",
    "std::string::String",
    "std::vec::Vec",
    "std::collections::HashMap",
    "std::collections::HashSet",
    "std::collections::BTreeMap",
    "std::collections::BTreeSet",
    "std::collections::VecDeque",
    "std::option::Option",
    "std::iter::Iterator",
    "language_detector::signatures::detectors::DetectionResults",
    "ralph_workflow::language_detector::signatures::detectors::DetectionResults",
];

declare_lint_pass!(ForbidMutatingReceiverMethods => [FORBID_MUTATING_RECEIVER_METHODS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_MUTATING_RECEIVER_METHODS]);
    lint_store.register_late_pass(|_| Box::new(ForbidMutatingReceiverMethods));
}

fn is_allowed_receiver_str(ty_str: &str) -> bool {
    let base = ty_str
        .strip_prefix("&mut ")
        .or_else(|| ty_str.strip_prefix("&"))
        .unwrap_or(ty_str);

    ALLOWED_RECEIVER_TYPES
        .iter()
        .any(|allowed| base.starts_with(allowed))
}

fn is_allowed_receiver_type(ty: Ty<'_>) -> bool {
    is_allowed_receiver_str(&format!("{ty}"))
}

const ALLOWED_METHODS: &[&str] = &[
    "next",
    "any",
    "all",
    "find",
    "find_map",
    "filter",
    "map",
    "flat_map",
    "fold",
    "reduce",
    "try_fold",
    "push",
    "push_unique",
    "extend",
    "insert",
    "clear",
    "take",
    "update_state",
    "add_step_bounded",
    "read_event_into",
    "execute",
    "flush",
    "update",
    "try_wait",
    "normalize_agent_chain_for_invocation",
    "capture_file_with_workspace",
    "capture_file_impl",
    "trim_text",
    "read",
    "read_json",
    "read_to_string",
    "read_tree",
    "position",
    "recurse_untracked_dirs",
    "include_untracked",
    "write_all",
    "write_str",
    "write",
    "set_current_message_id",
    "on_message_stop",
    "apply_ccs_aliases",
    "apply_agent_overrides",
    "set_ccs_aliases",
    "apply_unified_config",
    "set_opencode_catalog",
    "set_readonly",
    "replace_execution_history_bounded",
    "config_mut",
    "log_effect",
    "disarm",
    "intern_str",
    "intern_string",
    "capture_git_state",
    "add_path",
    "remove_path",
    "start",
    "register",
    "finish",
    "body_mut",
    "mark_owned",
    "field",
    "debug_struct",
    "watch",
    "custom_flags",
    "create_new",
    "set_mode",
];

impl<'tcx> LateLintPass<'tcx> for ForbidMutatingReceiverMethods {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        let ExprKind::MethodCall(method_name, receiver, _, _) = &expr.kind else {
            return;
        };

        let method_str = method_name.ident.name.as_str();
        if ALLOWED_METHODS.iter().any(|&m| m == method_str) {
            return;
        }

        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        let typeck = cx.typeck_results();
        let receiver_ty = typeck.expr_ty(receiver);

        if is_allowed_receiver_type(receiver_ty) {
            return;
        }

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
            diag.note(
                "if a mutating receiver is genuinely required for I/O handles or process \
                 objects, keep it at the boundary. Style guides: \
                 `docs/code-style/functional-transformations.md` and \
                 `docs/code-style/boundaries.md`.",
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
    fn allows_hashmap() {
        assert!(is_allowed_receiver_str(
            "std::collections::HashMap<String, String>"
        ));
    }

    #[test]
    fn allows_vec() {
        assert!(is_allowed_receiver_str("std::vec::Vec<i32>"));
    }

    #[test]
    fn rejects_custom_type() {
        assert!(!is_allowed_receiver_str("my_app::Config"));
    }
}
