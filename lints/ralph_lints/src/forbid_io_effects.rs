//! Lint: `FORBID_IO_EFFECTS`
//!
//! Rejects direct calls to I/O-performing standard library functions outside
//! of boundary modules. This covers:
//!
//! - `std::fs::*` — filesystem operations
//! - `std::env::*` — environment variable access, current directory
//! - `std::process::*` — process spawning
//! - `std::time::Instant::now()`, `std::time::SystemTime::now()` — clock reads
//!
//! ## FP principle: effects belong at the boundary
//!
//! In Haskell, all I/O is explicitly typed as `IO a` and can only occur in
//! the `IO` monad. This makes effectful code visible in type signatures and
//! pushes I/O to the program's edges.
//!
//! ## Boundary exceptions
//!
//! Boundary modules (`io/`, `runtime/`, `ffi/`, `boundary/`) are where I/O
//! naturally belongs. This lint does not fire in those modules.

use crate::boundary::is_in_boundary_module;
use rustc_hir::{Expr, ExprKind, QPath};
use rustc_lint::{LateContext, LateLintPass, LintContext};
use rustc_middle::ty::TyKind;
use rustc_session::{declare_lint, declare_lint_pass};

declare_lint! {
    /// ### What it does
    ///
    /// Rejects direct I/O operations outside boundary modules:
    /// - Filesystem access (`std::fs`)
    /// - Environment access (`std::env`)
    /// - Process spawning (`std::process`)
    /// - Clock reads (`std::time::Instant::now`, `std::time::SystemTime::now`)
    ///
    /// ### Example (bad — I/O in domain logic)
    ///
    /// ```rust,ignore
    /// fn load_config() -> Config {
    ///     let contents = std::fs::read_to_string("config.toml").unwrap();
    ///     parse_config(&contents)
    /// }
    /// ```
    ///
    /// ### Example (good — I/O in boundary, pure parsing in domain)
    ///
    /// ```rust,ignore
    /// // io/config_loader.rs
    /// fn read_config_file(path: &Path) -> Result<String, IoError> {
    ///     std::fs::read_to_string(path).map_err(IoError::from)
    /// }
    ///
    /// // domain/config.rs
    /// fn parse_config(contents: &str) -> Result<Config, ParseError> {
    ///     toml::from_str(contents).map_err(ParseError::from)
    /// }
    /// ```
    pub FORBID_IO_EFFECTS,
    Deny,
    "I/O effects (filesystem, env, process, clock) are forbidden outside boundary modules"
}

/// Effect categories for better error messages.
#[derive(Clone, Copy)]
enum EffectKind {
    Filesystem,
    Environment,
    Process,
    Clock,
}

impl EffectKind {
    const fn description(self) -> &'static str {
        match self {
            Self::Filesystem => "filesystem operation",
            Self::Environment => "environment access",
            Self::Process => "process operation",
            Self::Clock => "clock read",
        }
    }

    const fn style_guide_rule(self) -> &'static str {
        match self {
            Self::Filesystem => "call `std::fs`",
            Self::Environment => "inspect environment variables",
            Self::Process => "spawn processes",
            Self::Clock => "read the clock",
        }
    }
}

/// Patterns for detecting I/O effects by path prefix.
const EFFECT_PATTERNS: &[(&[&str], EffectKind)] = &[
    // Filesystem
    (&["std", "fs"], EffectKind::Filesystem),
    // Environment
    (&["std", "env"], EffectKind::Environment),
    // Process
    (&["std", "process"], EffectKind::Process),
];

/// Clock access methods that are forbidden (called on Instant or SystemTime).
const CLOCK_METHODS: &[&str] = &["now", "elapsed"];

/// Types that represent clock access.
const CLOCK_TYPES: &[&str] = &["std::time::Instant", "std::time::SystemTime"];

declare_lint_pass!(ForbidIoEffects => [FORBID_IO_EFFECTS]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_IO_EFFECTS]);
    lint_store.register_late_pass(|_| Box::new(ForbidIoEffects));
}

/// Check if a path matches an effect pattern.
fn path_matches_effect(def_path: &str) -> Option<EffectKind> {
    EFFECT_PATTERNS
        .iter()
        .find(|(pattern, _)| {
            let prefix = pattern.join("::");
            def_path.starts_with(&prefix)
        })
        .map(|(_, kind)| *kind)
}

/// Check if this is a clock access (Instant::now, SystemTime::now, etc.).
fn is_clock_access(cx: &LateContext<'_>, expr: &Expr<'_>) -> bool {
    match &expr.kind {
        // Method call: instant.elapsed()
        ExprKind::MethodCall(method, receiver, _, _) => {
            if !CLOCK_METHODS.contains(&method.ident.name.as_str()) {
                return false;
            }
            let ty = cx.typeck_results().expr_ty(receiver);
            let ty_str = format!("{ty}");
            CLOCK_TYPES.iter().any(|t| ty_str.starts_with(t))
        }
        // Static call: Instant::now()
        ExprKind::Call(func, _) => {
            if let ExprKind::Path(QPath::TypeRelative(ty, segment)) = &func.kind {
                if !CLOCK_METHODS.contains(&segment.ident.name.as_str()) {
                    return false;
                }
                // Check if the type is Instant or SystemTime
                if let rustc_hir::TyKind::Path(QPath::Resolved(_, path)) = &ty.kind {
                    let path_str = path
                        .segments
                        .iter()
                        .map(|s| s.ident.name.as_str())
                        .collect::<Vec<_>>()
                        .join("::");
                    return path_str.ends_with("Instant") || path_str.ends_with("SystemTime");
                }
            }
            false
        }
        _ => false,
    }
}

/// Get the def path for a function call if available.
fn get_call_def_path(cx: &LateContext<'_>, expr: &Expr<'_>) -> Option<String> {
    let ExprKind::Call(func, _) = &expr.kind else {
        return None;
    };

    let ExprKind::Path(qpath) = &func.kind else {
        return None;
    };

    let res = cx.typeck_results().qpath_res(qpath, func.hir_id);
    let rustc_hir::def::Res::Def(_, def_id) = res else {
        return None;
    };

    Some(cx.tcx.def_path_str(def_id))
}

/// Get the def path for a method call's definition.
fn get_method_def_path(cx: &LateContext<'_>, expr: &Expr<'_>) -> Option<String> {
    let ExprKind::MethodCall(_, _, _, _) = &expr.kind else {
        return None;
    };

    let def_id = cx.typeck_results().type_dependent_def_id(expr.hir_id)?;
    Some(cx.tcx.def_path_str(def_id))
}

/// Check if this is a process Command construction or method.
fn is_process_effect(cx: &LateContext<'_>, expr: &Expr<'_>) -> Option<EffectKind> {
    // Check method calls on Command, Child, etc.
    if let ExprKind::MethodCall(_, receiver, _, _) = &expr.kind {
        let ty = cx.typeck_results().expr_ty(receiver);
        let ty_str = format!("{ty}");
        if ty_str.starts_with("std::process::Command") || ty_str.starts_with("std::process::Child")
        {
            return Some(EffectKind::Process);
        }
    }

    // Check struct construction: Command::new()
    if let ExprKind::Call(func, _) = &expr.kind {
        if let ExprKind::Path(QPath::TypeRelative(ty, _)) = &func.kind {
            if let rustc_hir::TyKind::Path(QPath::Resolved(_, path)) = &ty.kind {
                let path_str = path
                    .segments
                    .iter()
                    .map(|s| s.ident.name.as_str())
                    .collect::<Vec<_>>()
                    .join("::");
                if path_str.ends_with("Command") {
                    // Check if it's std::process::Command
                    let full_ty = cx.typeck_results().expr_ty(func);
                    if let TyKind::FnDef(def_id, _) = full_ty.kind() {
                        let def_path = cx.tcx.def_path_str(*def_id);
                        if def_path.starts_with("std::process::Command") {
                            return Some(EffectKind::Process);
                        }
                    }
                }
            }
        }
    }

    None
}

fn emit_diagnostic(cx: &LateContext<'_>, expr: &Expr<'_>, kind: EffectKind, path: &str) {
    cx.span_lint(FORBID_IO_EFFECTS, expr.span, |diag| {
        diag.primary_message(format!(
            "{} `{}` is forbidden outside boundary modules",
            kind.description(),
            path
        ));
        diag.help(format!(
            "domain modules must not {}. Move this code to a boundary module \
             (io/, runtime/, ffi/, boundary/) and pass pure data to domain functions. \
             See `docs/code-style/boundaries.md`.",
            kind.style_guide_rule()
        ));
    });
}

impl<'tcx> LateLintPass<'tcx> for ForbidIoEffects {
    fn check_expr(&mut self, cx: &LateContext<'tcx>, expr: &'tcx Expr<'tcx>) {
        if is_in_boundary_module(cx, expr.span) {
            return;
        }

        // Check for clock access first (special case)
        if is_clock_access(cx, expr) {
            emit_diagnostic(cx, expr, EffectKind::Clock, "time access");
            return;
        }

        // Check for process effects
        if let Some(kind) = is_process_effect(cx, expr) {
            emit_diagnostic(cx, expr, kind, "std::process");
            return;
        }

        // Check function calls
        if let Some(def_path) = get_call_def_path(cx, expr) {
            if let Some(kind) = path_matches_effect(&def_path) {
                emit_diagnostic(cx, expr, kind, &def_path);
                return;
            }
        }

        // Check method calls
        if let Some(def_path) = get_method_def_path(cx, expr) {
            if let Some(kind) = path_matches_effect(&def_path) {
                emit_diagnostic(cx, expr, kind, &def_path);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{path_matches_effect, EffectKind};

    #[test]
    fn detects_fs_read() {
        let kind = path_matches_effect("std::fs::read_to_string");
        assert!(matches!(kind, Some(EffectKind::Filesystem)));
    }

    #[test]
    fn detects_env_var() {
        let kind = path_matches_effect("std::env::var");
        assert!(matches!(kind, Some(EffectKind::Environment)));
    }

    #[test]
    fn detects_env_current_dir() {
        let kind = path_matches_effect("std::env::current_dir");
        assert!(matches!(kind, Some(EffectKind::Environment)));
    }

    #[test]
    fn detects_process_command() {
        let kind = path_matches_effect("std::process::Command::new");
        assert!(matches!(kind, Some(EffectKind::Process)));
    }

    #[test]
    fn does_not_match_unrelated_path() {
        let kind = path_matches_effect("std::collections::HashMap::new");
        assert!(kind.is_none());
    }

    #[test]
    fn does_not_match_user_module() {
        let kind = path_matches_effect("my_crate::fs::read");
        assert!(kind.is_none());
    }
}
