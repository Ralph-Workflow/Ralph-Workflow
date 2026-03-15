use super::SignatureFiles;
use crate::workspace::Workspace;
use std::path::Path;

fn push_unique(vec: &mut Vec<String>, value: impl Into<String>) {
    let value = value.into();
    if !vec.iter().any(|v| v == &value) {
        vec.push(value);
    }
}

fn combine_unique(items: &[String]) -> Option<String> {
    match items.len() {
        0 => None,
        1 => Some(items[0].clone()),
        _ => Some(
            items
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>()
                .join(" + "),
        ),
    }
}

/// Detection results accumulator.
pub(super) struct DetectionResults {
    frameworks: Vec<String>,
    test_frameworks: Vec<String>,
    package_managers: Vec<String>,
}

impl DetectionResults {
    pub(super) const fn new() -> Self {
        Self {
            frameworks: Vec::new(),
            test_frameworks: Vec::new(),
            package_managers: Vec::new(),
        }
    }

    fn push_framework(&mut self, framework: impl Into<String>) {
        push_unique(&mut self.frameworks, framework);
    }

    fn push_test_framework(&mut self, framework: impl Into<String>) {
        push_unique(&mut self.test_frameworks, framework);
    }

    fn push_package_manager(&mut self, manager: impl Into<String>) {
        push_unique(&mut self.package_managers, manager);
    }

    pub(super) fn finish(self) -> (Vec<String>, Option<String>, Option<String>) {
        (
            self.frameworks,
            combine_unique(&self.test_frameworks),
            combine_unique(&self.package_managers),
        )
    }
}

pub(super) fn detect_rust(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(cargo_files) = signatures.by_name_lower.get("cargo.toml") else {
        return;
    };

    results.push_package_manager("Cargo");

    for path in cargo_files {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("[dev-dependencies]") || content_lower.contains("[[test]]") {
            results.push_test_framework("cargo test");
        }

        for (name, framework) in [
            ("actix", "Actix"),
            ("axum", "Axum"),
            ("rocket", "Rocket"),
            ("tokio", "Tokio"),
            ("warp", "Warp"),
            ("tauri", "Tauri"),
            ("leptos", "Leptos"),
            ("yew", "Yew"),
        ] {
            if content_lower.contains(name) {
                results.push_framework(framework);
            }
        }
    }
}

pub(super) fn detect_python(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let paths = if let Some(p) = signatures.by_name_lower.get("pyproject.toml") {
        results.push_package_manager("Poetry/pip");
        Some(p)
    } else if let Some(p) = signatures.by_name_lower.get("requirements.txt") {
        results.push_package_manager("pip");
        Some(p)
    } else if signatures.by_name_lower.contains_key("setup.py") {
        results.push_package_manager("setuptools");
        None
    } else if signatures.by_name_lower.contains_key("pipfile") {
        results.push_package_manager("Pipenv");
        None
    } else {
        None
    };

    if let Some(paths) = paths {
        for path in paths {
            let Ok(content) = workspace.read(path) else {
                continue;
            };
            let content_lower = content.to_lowercase();

            if content_lower.contains("pytest") {
                results.push_test_framework("pytest");
            }

            for (name, framework) in [
                ("django", "Django"),
                ("fastapi", "FastAPI"),
                ("flask", "Flask"),
            ] {
                if content_lower.contains(name) {
                    results.push_framework(framework);
                }
            }
        }
    }
}

pub(super) fn detect_javascript(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(paths) = signatures.by_name_lower.get("package.json") else {
        return;
    };

    if signatures.by_name_lower.contains_key("pnpm-lock.yaml") {
        results.push_package_manager("pnpm");
    } else if signatures.by_name_lower.contains_key("yarn.lock") {
        results.push_package_manager("Yarn");
    } else if signatures.by_name_lower.contains_key("bun.lockb")
        || signatures.by_name_lower.contains_key("bun.lock")
    {
        results.push_package_manager("Bun");
    } else {
        results.push_package_manager("npm");
    }

    for path in paths {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        for (pattern, name) in [
            ("\"jest\"", "Jest"),
            ("\"vitest\"", "Vitest"),
            ("\"mocha\"", "Mocha"),
            ("\"cypress\"", "Cypress"),
            ("\"playwright\"", "Playwright"),
        ] {
            if content_lower.contains(pattern) {
                results.push_test_framework(name);
            }
        }

        for (pattern, name) in [
            ("\"react\"", "React"),
            ("\"vue\"", "Vue"),
            ("\"angular\"", "Angular"),
            ("\"svelte\"", "Svelte"),
            ("\"next\"", "Next.js"),
            ("\"nuxt\"", "Nuxt"),
            ("\"express\"", "Express"),
            ("\"fastify\"", "Fastify"),
            ("\"nestjs\"", "NestJS"),
            ("\"gatsby\"", "Gatsby"),
        ] {
            if content_lower.contains(pattern) {
                results.push_framework(name);
            }
        }
    }
}

pub(super) fn detect_go(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(paths) = signatures.by_name_lower.get("go.mod") else {
        return;
    };

    results.push_package_manager("Go Modules");
    results.push_test_framework("go test");

    for path in paths {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        for (pattern, name) in [
            ("gin-gonic/gin", "Gin"),
            ("labstack/echo", "Echo"),
            ("gofiber/fiber", "Fiber"),
            ("gorilla/mux", "Gorilla"),
            ("go-chi/chi", "Chi"),
        ] {
            if content_lower.contains(pattern) {
                results.push_framework(name);
            }
        }
    }
}

pub(super) fn detect_ruby(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(paths) = signatures.by_name_lower.get("gemfile") else {
        return;
    };

    results.push_package_manager("Bundler");

    for path in paths {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("rspec") {
            results.push_test_framework("RSpec");
        } else if content_lower.contains("minitest") {
            results.push_test_framework("Minitest");
        }

        if content_lower.contains("rails") {
            results.push_framework("Rails");
        } else if content_lower.contains("sinatra") {
            results.push_framework("Sinatra");
        }
    }
}

pub(super) fn detect_java(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    if let Some(paths) = signatures.by_name_lower.get("pom.xml") {
        results.push_package_manager("Maven");
        detect_java_frameworks(workspace, paths, results);
    }

    let gradle_paths: Vec<_> = signatures
        .by_name_lower
        .get("build.gradle")
        .into_iter()
        .chain(signatures.by_name_lower.get("build.gradle.kts"))
        .flatten()
        .collect();

    if !gradle_paths.is_empty() {
        results.push_package_manager("Gradle");
        detect_java_frameworks(workspace, &gradle_paths, results);
    }
}

fn detect_java_frameworks(
    workspace: &dyn Workspace,
    paths: &[impl AsRef<Path>],
    results: &mut DetectionResults,
) {
    for path in paths {
        let Ok(content) = workspace.read(path.as_ref()) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("junit") {
            results.push_test_framework("JUnit");
        }

        if content_lower.contains("spring") {
            results.push_framework("Spring");
        }
    }
}

pub(super) fn detect_php(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(paths) = signatures.by_name_lower.get("composer.json") else {
        return;
    };

    results.push_package_manager("Composer");

    for path in paths {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("phpunit") {
            results.push_test_framework("PHPUnit");
        }

        for (pattern, name) in [("laravel", "Laravel"), ("symfony", "Symfony")] {
            if content_lower.contains(pattern) {
                results.push_framework(name);
            }
        }
    }
}

pub(super) fn detect_dotnet(signatures: &SignatureFiles, results: &mut DetectionResults) {
    if signatures
        .by_name_lower
        .keys()
        .any(|k| k.ends_with(".csproj") || k.ends_with(".fsproj"))
    {
        results.push_package_manager("NuGet");
    }
}

pub(super) fn detect_elixir(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(paths) = signatures.by_name_lower.get("mix.exs") else {
        return;
    };

    results.push_package_manager("Mix");
    results.push_test_framework("ExUnit");

    for path in paths {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("phoenix") {
            results.push_framework("Phoenix");
        }
    }
}

pub(super) fn detect_dart(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: &mut DetectionResults,
) {
    let Some(paths) = signatures.by_name_lower.get("pubspec.yaml") else {
        return;
    };

    results.push_package_manager("Pub");

    for path in paths {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("flutter:") || content_lower.contains("flutter_test") {
            results.push_framework("Flutter");
            results.push_test_framework("Flutter Test");
        }
    }
}
