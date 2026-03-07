import { useState } from "react";
import { reviewPromptWithAi } from "../../api/tauri";
import type { PromptReviewResult } from "../../types";

interface PromptReviewPanelProps {
  promptContent: string;
  onApplyImprovedPrompt: (improved: string) => void;
}

type PanelState = "idle" | "loading" | "success" | "error" | "no-key";

export function PromptReviewPanel({
  promptContent,
  onApplyImprovedPrompt,
}: PromptReviewPanelProps) {
  const [panelState, setPanelState] = useState<PanelState>("idle");
  const [result, setResult] = useState<PromptReviewResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handleReview() {
    setPanelState("loading");
    setResult(null);
    setErrorMsg(null);
    try {
      const res = await reviewPromptWithAi(promptContent);
      setResult(res);
      setPanelState("success");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg.includes("ANTHROPIC_API_KEY")) {
        setPanelState("no-key");
      } else {
        setPanelState("error");
        setErrorMsg(msg);
      }
    }
  }

  return (
    <div
      data-testid="prompt-review-panel"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-md)",
        padding: "14px 16px",
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          AI Review
        </span>
        <button
          className="btn btn-secondary"
          style={{ fontSize: 12, padding: "4px 12px" }}
          onClick={() => void handleReview()}
          disabled={panelState === "loading"}
          data-testid="review-button"
        >
          {panelState === "loading" ? "Reviewing…" : "Review prompt"}
        </button>
      </div>

      {panelState === "loading" && (
        <div
          data-testid="loading-indicator"
          style={{
            fontSize: 12,
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          Analysing your prompt…
        </div>
      )}

      {panelState === "no-key" && (
        <div
          data-testid="no-key-message"
          style={{
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}
        >
          AI review requires an Anthropic API key. Set{" "}
          <code
            style={{
              fontFamily: "var(--font-mono)",
              background: "var(--bg-surface)",
              padding: "1px 4px",
              borderRadius: 3,
            }}
          >
            ANTHROPIC_API_KEY
          </code>{" "}
          in your environment or add{" "}
          <code style={{ fontFamily: "var(--font-mono)" }}>
            api_key = "..."
          </code>{" "}
          under{" "}
          <code style={{ fontFamily: "var(--font-mono)" }}>[gui]</code> in{" "}
          <code style={{ fontFamily: "var(--font-mono)" }}>
            ~/.ralph/config.toml
          </code>
          .
        </div>
      )}

      {panelState === "error" && errorMsg && (
        <div
          data-testid="error-message"
          style={{
            fontSize: 12,
            color: "var(--status-failed)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {errorMsg}
        </div>
      )}

      {panelState === "success" && result && (
        <div data-testid="suggestions-list" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {result.suggestions.length > 0 && (
            <div>
              <div
                className="section-label"
                style={{ marginBottom: 6, fontSize: 11 }}
              >
                Suggestions
              </div>
              <ul
                style={{
                  margin: 0,
                  paddingLeft: 18,
                  listStyle: "disc",
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                }}
              >
                {result.suggestions.map((s, i) => (
                  <li
                    key={i}
                    style={{
                      fontSize: 12,
                      color: "var(--text-secondary)",
                      fontFamily: "var(--font-mono)",
                    }}
                  >
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {result.improved_prompt && (
            <button
              className="btn btn-primary"
              style={{ alignSelf: "flex-start", fontSize: 12 }}
              onClick={() => onApplyImprovedPrompt(result.improved_prompt ?? "")}
              data-testid="apply-improved-button"
            >
              Apply improved version
            </button>
          )}
        </div>
      )}

      {panelState === "idle" && (
        <div
          style={{
            fontSize: 12,
            color: "var(--text-muted)",
          }}
        >
          Click &ldquo;Review prompt&rdquo; to get AI-powered improvement suggestions.
        </div>
      )}
    </div>
  );
}
