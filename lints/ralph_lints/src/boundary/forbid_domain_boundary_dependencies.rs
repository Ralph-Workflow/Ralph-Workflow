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
            let segments = &use_tree.prefix.segments;
            if let Some(first_segment) = segments.first() {
                let name = first_segment.ident.name.as_str();
                if BOUNDARY_MODULES.iter().any(|&b| b == name) {
                    cx.span_lint(FORBID_DOMAIN_BOUNDARY_DEPENDENCIES, item.span, |diag| {
                        diag.primary_message(format!(
                            "import from boundary module `{}` is forbidden in non-boundary modules",
                            name
                        ));
                        diag.help(
                            "move I/O to boundary layer: imports from boundary modules (io/, runtime/, ffi/, boundary/) should only exist in boundary modules themselves",
                        );
                    });
                }
            }
        }
    }
}
