use crate::domain::boundary::{is_in_boundary_module, BOUNDARY_MODULES};
use rustc_ast::ast::Item;
use rustc_lint::{EarlyContext, EarlyLintPass, LintContext};
use rustc_session::{declare_lint, impl_lint_pass};

declare_lint! {
    pub FORBID_DOMAIN_BOUNDARY_DEPENDENCIES,
    Deny,
    "imports from boundary modules (io/, runtime/, ffi/, boundary/) are forbidden in non-boundary modules"
}

impl_lint_pass!(ForbidDomainBoundaryDependencies => [FORBID_DOMAIN_BOUNDARY_DEPENDENCIES]);

pub fn register_lints(_sess: &rustc_session::Session, lint_store: &mut rustc_lint::LintStore) {
    lint_store.register_lints(&[FORBID_DOMAIN_BOUNDARY_DEPENDENCIES]);
    lint_store.register_early_pass(|| Box::new(ForbidDomainBoundaryDependencies));
}

pub struct ForbidDomainBoundaryDependencies;

impl EarlyLintPass for ForbidDomainBoundaryDependencies {
    fn check_item(&mut self, cx: &EarlyContext<'_>, item: &Item) {
        if is_in_boundary_module(cx, item.span) {
            return;
        }

        if let rustc_ast::ast::ItemKind::Use(use_tree) = &item.kind {
            let Some(name) = boundary_segment_in_use_tree(use_tree) else {
                return;
            };

            cx.span_lint(FORBID_DOMAIN_BOUNDARY_DEPENDENCIES, item.span, |diag| {
                diag.primary_message(format!(
                    "import from boundary module `{}` is forbidden in non-boundary modules",
                    name
                ));
                diag.help(
                    "move I/O to boundary layer: imports from boundary modules (io/, runtime/, ffi/, boundary/, executor/, and other boundary markers) should only exist in boundary modules themselves",
                );
            });
        }
    }
}

/// Check if a use tree imports from a boundary module.
///
/// Returns the boundary module name if found, None otherwise.
///
/// Excludes `std` and `core` imports to avoid false positives:
/// `std::io`, `std::fs`, `std::env`, etc. are standard library modules,
/// NOT boundary modules. Only project-local paths like `crate::io::foo`
/// or `crate::runtime::bar` would be genuine boundary imports.
fn boundary_segment_in_use_tree(use_tree: &rustc_ast::ast::UseTree) -> Option<String> {
    let segments: Vec<&str> = use_tree
        .prefix
        .segments
        .iter()
        .map(|segment| segment.ident.name.as_str())
        .collect();

    // std::... and core::... are the standard library, not boundary modules.
    // Skip them to avoid false positives on std::io, std::fs, std::env, etc.
    if let Some(first) = segments.first() {
        if *first == "std" || *first == "core" {
            return None;
        }
    }

    for segment in &segments {
        if BOUNDARY_MODULES.iter().any(|boundary| *boundary == *segment) {
            return Some((*segment).to_string());
        }
    }
    None
}
