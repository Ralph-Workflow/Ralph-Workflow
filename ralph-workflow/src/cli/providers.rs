//! Provider listing and information display.
//!
//! Contains functions for displaying `OpenCode` provider information.

use crate::agents::OpenCodeProviderType;
use crate::logger::Colors;

trait StdIoWriteCompat {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()>;
}

impl<T: std::io::Write> StdIoWriteCompat for T {
    fn write_fmt(&mut self, args: std::fmt::Arguments<'_>) -> std::io::Result<()> {
        std::io::Write::write_fmt(self, args)
    }
}

/// Provider category for display organization
#[derive(Debug, Clone, Copy)]
struct ProviderCategory {
    name: &'static str,
    providers: &'static [(OpenCodeProviderType, &'static str)],
}

/// Provider categories with their providers and example aliases
const PROVIDER_CATEGORIES: &[ProviderCategory] = &[
    ProviderCategory {
        name: "OPENCODE GATEWAY",
        providers: &[(OpenCodeProviderType::OpenCodeZen, "opencode-zen-glm")],
    },
    ProviderCategory {
        name: "CHINESE AI PROVIDERS",
        providers: &[
            (OpenCodeProviderType::ZaiDirect, "opencode-zai-glm"),
            (
                OpenCodeProviderType::ZaiCodingPlan,
                "opencode-zai-glm-codingplan",
            ),
            (OpenCodeProviderType::Moonshot, "opencode-moonshot"),
            (OpenCodeProviderType::MiniMax, "opencode-minimax"),
        ],
    },
    ProviderCategory {
        name: "MAJOR CLOUD PROVIDERS",
        providers: &[
            (OpenCodeProviderType::Anthropic, "opencode-direct-claude"),
            (OpenCodeProviderType::OpenAI, "opencode-openai"),
            (OpenCodeProviderType::Google, "opencode-google"),
            (OpenCodeProviderType::GoogleVertex, "opencode-vertex"),
            (OpenCodeProviderType::AmazonBedrock, "opencode-bedrock"),
            (OpenCodeProviderType::AzureOpenAI, "opencode-azure"),
            (OpenCodeProviderType::GithubCopilot, "opencode-copilot"),
        ],
    },
    ProviderCategory {
        name: "FAST INFERENCE PROVIDERS",
        providers: &[
            (OpenCodeProviderType::Groq, "opencode-groq"),
            (OpenCodeProviderType::Together, "opencode-together"),
            (OpenCodeProviderType::Fireworks, "opencode-fireworks"),
            (OpenCodeProviderType::Cerebras, "opencode-cerebras"),
            (OpenCodeProviderType::SambaNova, "opencode-sambanova"),
            (OpenCodeProviderType::DeepInfra, "opencode-deepinfra"),
        ],
    },
    ProviderCategory {
        name: "GATEWAY PROVIDERS",
        providers: &[
            (OpenCodeProviderType::OpenRouter, "opencode-openrouter"),
            (OpenCodeProviderType::Cloudflare, "opencode-cloudflare"),
        ],
    },
    ProviderCategory {
        name: "SPECIALIZED PROVIDERS",
        providers: &[
            (OpenCodeProviderType::DeepSeek, "opencode-deepseek"),
            (OpenCodeProviderType::Xai, "opencode-xai"),
            (OpenCodeProviderType::Mistral, "opencode-mistral"),
            (OpenCodeProviderType::Cohere, "opencode-cohere"),
            (OpenCodeProviderType::Perplexity, "opencode-perplexity"),
            (OpenCodeProviderType::AI21, "opencode-ai21"),
            (OpenCodeProviderType::VeniceAI, "opencode-venice"),
        ],
    },
    ProviderCategory {
        name: "OPEN-SOURCE MODEL PROVIDERS",
        providers: &[
            (OpenCodeProviderType::HuggingFace, "opencode-huggingface"),
            (OpenCodeProviderType::Replicate, "opencode-replicate"),
        ],
    },
    ProviderCategory {
        name: "CLOUD PLATFORM PROVIDERS",
        providers: &[
            (OpenCodeProviderType::Baseten, "opencode-baseten"),
            (OpenCodeProviderType::Cortecs, "opencode-cortecs"),
            (OpenCodeProviderType::Scaleway, "opencode-scaleway"),
            (OpenCodeProviderType::OVHcloud, "opencode-ovhcloud"),
            (OpenCodeProviderType::IONet, "opencode-ionet"),
            (OpenCodeProviderType::Nebius, "opencode-nebius"),
        ],
    },
    ProviderCategory {
        name: "AI GATEWAY PROVIDERS",
        providers: &[
            (OpenCodeProviderType::Vercel, "opencode-vercel"),
            (OpenCodeProviderType::Helicone, "opencode-helicone"),
            (OpenCodeProviderType::ZenMux, "opencode-zenmux"),
        ],
    },
    ProviderCategory {
        name: "ENTERPRISE PROVIDERS",
        providers: &[
            (OpenCodeProviderType::SapAICore, "opencode-sap"),
            (
                OpenCodeProviderType::AzureCognitiveServices,
                "opencode-azure-cognitive",
            ),
        ],
    },
    ProviderCategory {
        name: "LOCAL PROVIDERS",
        providers: &[
            (OpenCodeProviderType::Ollama, "opencode-ollama"),
            (OpenCodeProviderType::LMStudio, "opencode-lmstudio"),
            (OpenCodeProviderType::OllamaCloud, "opencode-ollama-cloud"),
            (OpenCodeProviderType::LlamaCpp, "opencode-llamacpp"),
        ],
    },
    ProviderCategory {
        name: "CUSTOM",
        providers: &[(OpenCodeProviderType::Custom, "(custom)")],
    },
];

/// Helper function to print provider information for --list-providers.
pub fn print_provider_info(colors: Colors, provider: OpenCodeProviderType, agent_alias: &str) {
    let examples = provider.example_models();
    let example_str = if examples.is_empty() {
        String::new()
    } else {
        format!(" (e.g., {})", examples[0])
    };

    let _ = writeln!(
        std::io::stdout(),
        "{}{}{}",
        colors.bold(),
        provider.name(),
        colors.reset()
    );
    let _ = writeln!(
        std::io::stdout(),
        "  Prefix: {}{}",
        provider.prefix(),
        example_str
    );
    let _ = writeln!(std::io::stdout(), "  Auth: {}", provider.auth_command());
    let _ = writeln!(std::io::stdout(), "  Agent: {agent_alias}");
}

/// Print a provider category with all its providers
fn print_category(colors: Colors, category: &ProviderCategory) {
    let _ = writeln!(
        std::io::stdout(),
        "{}═══ {} ═══{}",
        colors.bold(),
        category.name,
        colors.reset()
    );
    category
        .providers
        .iter()
        .for_each(|(provider, alias)| print_provider_info(colors, *provider, alias));
    let _ = writeln!(std::io::stdout());
}

/// Print important notes about providers
fn print_notes(colors: Colors) {
    let _ = writeln!(
        std::io::stdout(),
        "{}═══ IMPORTANT NOTES ═══{}",
        colors.bold(),
        colors.reset()
    );
    let _ = writeln!(
        std::io::stdout(),
        "• OpenCode Zen (opencode/*) and Z.AI Direct (zai/* or zhipuai/*) are SEPARATE endpoints!"
    );
    let _ = writeln!(
        std::io::stdout(),
        "  - opencode/* routes through OpenCode's Zen gateway at opencode.ai"
    );
    let _ = writeln!(
        std::io::stdout(),
        "  - zai/* or zhipuai/* connects directly to Z.AI's API at api.z.ai"
    );
    let _ = writeln!(
        std::io::stdout(),
        "  - Z.AI Coding Plan is an auth tier; model prefix remains zai/* or zhipuai/*"
    );
    let _ = writeln!(
        std::io::stdout(),
        "• Cloud providers (Vertex, Bedrock, Azure, SAP) require additional configuration"
    );
    let _ = writeln!(
        std::io::stdout(),
        "• Local providers (Ollama, LM Studio, llama.cpp) run on your hardware - no API key needed"
    );
    let _ = writeln!(
        std::io::stdout(),
        "• Use clear naming: opencode-zen-*, opencode-zai-*, opencode-direct-* aliases"
    );
    let _ = writeln!(std::io::stdout());
}

/// Handle --list-providers command.
///
/// Displays a categorized list of all `OpenCode` provider types with their
/// model prefixes, authentication commands, and example agent aliases.
pub fn handle_list_providers(colors: Colors) {
    let _ = writeln!(
        std::io::stdout(),
        "{}OpenCode Provider Types{}",
        colors.bold(),
        colors.reset()
    );
    let _ = writeln!(std::io::stdout());
    let _ = writeln!(std::io::stdout(), "Ralph includes built-in guidance for major OpenCode provider prefixes (plus a custom fallback).");
    let _ = writeln!(
        std::io::stdout(),
        "OpenCode may support additional providers; consult OpenCode docs for the full set."
    );
    let _ = writeln!(std::io::stdout());

    PROVIDER_CATEGORIES.iter().for_each(|category| {
        print_category(colors, category);
    });

    print_notes(colors);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_provider_categories_count() {
        // Verify we have all expected categories
        assert_eq!(PROVIDER_CATEGORIES.len(), 12);
    }

    #[test]
    fn test_category_names() {
        let expected_names: &[&str] = &[
            "OPENCODE GATEWAY",
            "CHINESE AI PROVIDERS",
            "MAJOR CLOUD PROVIDERS",
            "FAST INFERENCE PROVIDERS",
            "GATEWAY PROVIDERS",
            "SPECIALIZED PROVIDERS",
            "OPEN-SOURCE MODEL PROVIDERS",
            "CLOUD PLATFORM PROVIDERS",
            "AI GATEWAY PROVIDERS",
            "ENTERPRISE PROVIDERS",
            "LOCAL PROVIDERS",
            "CUSTOM",
        ];
        let actual_names: Vec<_> = PROVIDER_CATEGORIES.iter().map(|c| c.name).collect();
        assert_eq!(actual_names, expected_names);
    }

    #[test]
    fn test_no_empty_categories() {
        PROVIDER_CATEGORIES.iter().for_each(|category| {
            assert!(
                !category.providers.is_empty(),
                "Category '{}' is empty",
                category.name
            );
        });
    }

    #[test]
    fn test_all_providers_have_aliases() {
        PROVIDER_CATEGORIES
            .iter()
            .flat_map(|category| category.providers.iter())
            .for_each(|(provider, alias)| {
                assert!(!alias.is_empty(), "Provider {provider:?} has empty alias");
            });
    }
}
