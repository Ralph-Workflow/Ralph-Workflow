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

    #[must_use]
    pub fn with_framework(mut self, framework: impl Into<String>) -> Self {
        push_unique(&mut self.frameworks, framework);
        self
    }

    #[must_use]
    pub fn with_test_framework(mut self, framework: impl Into<String>) -> Self {
        push_unique(&mut self.test_frameworks, framework);
        self
    }

    #[must_use]
    pub fn with_package_manager(mut self, manager: impl Into<String>) -> Self {
        push_unique(&mut self.package_managers, manager);
        self
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
    results: DetectionResults,
) -> DetectionResults {
    let Some(cargo_files) = signatures.by_name_lower.get("cargo.toml") else {
        return results;
    };

    let mut results = results.with_package_manager("Cargo");

    for path in cargo_files.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("[dev-dependencies]") || content_lower.contains("[[test]]") {
            results = results.with_test_framework("cargo test");
        }

        let frameworks: Vec<_> = [
            ("actix", "Actix"),
            ("axum", "Axum"),
            ("rocket", "Rocket"),
            ("tokio", "Tokio"),
            ("warp", "Warp"),
            ("tauri", "Tauri"),
            ("leptos", "Leptos"),
            ("yew", "Yew"),
        ]
        .into_iter()
        .filter(|(name, _)| content_lower.contains(name))
        .map(|(_, framework)| framework)
        .collect();

        for framework in frameworks {
            results = results.with_framework(framework);
        }
    }

    results
}

pub(super) fn detect_python(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let paths = if let Some(p) = signatures.by_name_lower.get("pyproject.toml") {
        Some((p, "Poetry/pip"))
    } else if let Some(p) = signatures.by_name_lower.get("requirements.txt") {
        Some((p, "pip"))
    } else if signatures.by_name_lower.contains_key("setup.py") {
        return results.with_package_manager("setuptools");
    } else if signatures.by_name_lower.contains_key("pipfile") {
        return results.with_package_manager("Pipenv");
    } else {
        None
    };

    let Some((paths, pkg_mgr)) = paths else {
        return results;
    };

    let mut results = results.with_package_manager(pkg_mgr);

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("pytest") {
            results = results.with_test_framework("pytest");
        }

        let frameworks: Vec<_> = [
            ("django", "Django"),
            ("fastapi", "FastAPI"),
            ("flask", "Flask"),
        ]
        .into_iter()
        .filter(|(name, _)| content_lower.contains(name))
        .map(|(_, framework)| framework)
        .collect();

        for framework in frameworks {
            results = results.with_framework(framework);
        }
    }

    results
}

pub(super) fn detect_javascript(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("package.json") else {
        return results;
    };

    let pkg_mgr = if signatures.by_name_lower.contains_key("pnpm-lock.yaml") {
        "pnpm"
    } else if signatures.by_name_lower.contains_key("yarn.lock") {
        "Yarn"
    } else if signatures.by_name_lower.contains_key("bun.lockb")
        || signatures.by_name_lower.contains_key("bun.lock")
    {
        "Bun"
    } else {
        "npm"
    };

    let mut results = results.with_package_manager(pkg_mgr);

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        let test_frameworks: Vec<_> = [
            ("\"jest\"", "Jest"),
            ("\"vitest\"", "Vitest"),
            ("\"mocha\"", "Mocha"),
            ("\"cypress\"", "Cypress"),
            ("\"playwright\"", "Playwright"),
        ]
        .into_iter()
        .filter(|(pattern, _)| content_lower.contains(pattern))
        .map(|(_, name)| name)
        .collect();

        for name in test_frameworks {
            results = results.with_test_framework(name);
        }

        let frameworks: Vec<_> = [
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
        ]
        .into_iter()
        .filter(|(pattern, _)| content_lower.contains(pattern))
        .map(|(_, name)| name)
        .collect();

        for name in frameworks {
            results = results.with_framework(name);
        }
    }

    results
}

pub(super) fn detect_go(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("go.mod") else {
        return results;
    };

    let mut results = results.with_package_manager("Go Modules");
    results = results.with_test_framework("go test");

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        let frameworks: Vec<_> = [
            ("gin-gonic/gin", "Gin"),
            ("labstack/echo", "Echo"),
            ("gofiber/fiber", "Fiber"),
            ("gorilla/mux", "Gorilla"),
            ("go-chi/chi", "Chi"),
        ]
        .into_iter()
        .filter(|(pattern, _)| content_lower.contains(pattern))
        .map(|(_, name)| name)
        .collect();

        for name in frameworks {
            results = results.with_framework(name);
        }
    }

    results
}

pub(super) fn detect_ruby(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("gemfile") else {
        return results;
    };

    let mut results = results.with_package_manager("Bundler");

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("rspec") {
            results = results.with_test_framework("RSpec");
        } else if content_lower.contains("minitest") {
            results = results.with_test_framework("Minitest");
        }

        if content_lower.contains("rails") {
            results = results.with_framework("Rails");
        } else if content_lower.contains("sinatra") {
            results = results.with_framework("Sinatra");
        }
    }

    results
}

pub(super) fn detect_java(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let results = if let Some(paths) = signatures.by_name_lower.get("pom.xml") {
        let results = results.with_package_manager("Maven");
        detect_java_frameworks(workspace, paths, results)
    } else {
        results
    };

    let gradle_paths: Vec<_> = signatures
        .by_name_lower
        .get("build.gradle")
        .into_iter()
        .chain(signatures.by_name_lower.get("build.gradle.kts"))
        .flatten()
        .collect();

    if gradle_paths.is_empty() {
        return results;
    }

    let results = results.with_package_manager("Gradle");
    detect_java_frameworks(workspace, &gradle_paths, results)
}

fn detect_java_frameworks(
    workspace: &dyn Workspace,
    paths: &[impl AsRef<Path>],
    mut results: DetectionResults,
) -> DetectionResults {
    for path in paths.iter() {
        let path_ref: &Path = path.as_ref();
        let Ok(content) = workspace.read(path_ref) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("junit") {
            results = results.with_test_framework("JUnit");
        }

        if content_lower.contains("spring") {
            results = results.with_framework("Spring");
        }
    }

    results
}

pub(super) fn detect_php(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("composer.json") else {
        return results;
    };

    let mut results = results.with_package_manager("Composer");

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("phpunit") {
            results = results.with_test_framework("PHPUnit");
        }

        let frameworks: Vec<_> = [("laravel", "Laravel"), ("symfony", "Symfony")]
            .into_iter()
            .filter(|(pattern, _)| content_lower.contains(pattern))
            .map(|(_, name)| name)
            .collect();

        for name in frameworks {
            results = results.with_framework(name);
        }
    }

    results
}

pub(super) fn detect_dotnet(
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    if signatures
        .by_name_lower
        .keys()
        .any(|k| k.ends_with(".csproj") || k.ends_with(".fsproj"))
    {
        results.with_package_manager("NuGet")
    } else {
        results
    }
}

pub(super) fn detect_elixir(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("mix.exs") else {
        return results;
    };

    let mut results = results.with_package_manager("Mix");
    results = results.with_test_framework("ExUnit");

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("phoenix") {
            results = results.with_framework("Phoenix");
        }
    }

    results
}

pub(super) fn detect_dart(
    workspace: &dyn Workspace,
    signatures: &SignatureFiles,
    results: DetectionResults,
) -> DetectionResults {
    let Some(paths) = signatures.by_name_lower.get("pubspec.yaml") else {
        return results;
    };

    let mut results = results.with_package_manager("Pub");

    for path in paths.iter() {
        let Ok(content) = workspace.read(path) else {
            continue;
        };
        let content_lower = content.to_lowercase();

        if content_lower.contains("flutter:") || content_lower.contains("flutter_test") {
            results = results.with_framework("Flutter");
            results = results.with_test_framework("Flutter Test");
        }
    }

    results
}
