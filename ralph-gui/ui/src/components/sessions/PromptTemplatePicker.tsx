import { PROMPT_TEMPLATES } from "./promptTemplates";

interface PromptTemplatePickerProps {
  onSelect: (content: string) => void;
}

export function PromptTemplatePicker({ onSelect }: PromptTemplatePickerProps) {
  return (
    <div>
      <div
        className="section-label"
        style={{ marginBottom: "var(--space-4)" }}
      >
        Choose a starting template
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
          gap: 10,
        }}
      >
        {PROMPT_TEMPLATES.map((tpl) => (
          <button
            key={tpl.id}
            data-testid={`template-${tpl.id}`}
            onClick={() => onSelect(tpl.content)}
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-start",
              gap: 6,
              padding: "14px 16px",
              background: "var(--bg-surface)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              cursor: "pointer",
              textAlign: "left",
              transition: "border-color var(--transition-fast)",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--accent)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.borderColor =
                "var(--border-subtle)";
            }}
          >
            <span
              style={{
                fontFamily: "var(--font-display)",
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-primary)",
                letterSpacing: "-0.01em",
              }}
            >
              {tpl.label}
            </span>
            <span
              style={{
                fontSize: 11,
                color: "var(--text-muted)",
                lineHeight: 1.5,
              }}
            >
              {tpl.description}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
