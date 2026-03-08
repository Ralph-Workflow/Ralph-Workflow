import { useEffect, useRef, useState } from "react";
import { useAppSelector, useAppDispatch } from "../store";
import {
  fetchGlobalConfig,
  fetchEffectiveConfig,
  fetchAiApiKey,
  saveAiApiKeyThunk,
  resetAiApiKeySaveStatus,
  setDirty,
} from "../store/slices/configSlice";
import {
  saveGlobalConfig,
  saveProjectConfig,
  getRawGlobalConfigToml,
  getRawProjectConfigToml,
  validateConfigToml,
} from "../api/tauri";
import type { ConfigView } from "../types";

type TabId = "effective" | "global" | "project";

interface ConfigFieldProps {
  label: string;
  description: string;
  value: string | number | boolean;
}

function ConfigField({ label, description, value }: ConfigFieldProps) {
  const displayValue =
    typeof value === "boolean" ? (value ? "true" : "false") : String(value);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        padding: "10px 0",
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      <div style={{ flex: 1 }}>
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 13,
            color: "var(--text-primary)",
            marginBottom: 2,
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: 11,
            color: "var(--text-muted)",
          }}
        >
          {description}
        </div>
      </div>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: 13,
          color:
            typeof value === "boolean"
              ? value
                ? "var(--status-completed)"
                : "var(--text-muted)"
              : "var(--accent)",
          marginLeft: 24,
          flexShrink: 0,
        }}
      >
        {displayValue}
      </div>
    </div>
  );
}

function ConfigTable({ config }: { config: ConfigView }) {
  return (
    <div>
      <ConfigField
        label="verbosity"
        description="Log verbosity level (0=quiet, 1=normal, 2=verbose)"
        value={config.verbosity}
      />
      <ConfigField
        label="developer_iters"
        description="Number of developer agent iterations per cycle"
        value={config.developer_iters}
      />
      <ConfigField
        label="reviewer_reviews"
        description="Number of reviewer passes per cycle"
        value={config.reviewer_reviews}
      />
      <ConfigField
        label="max_dev_continuations"
        description="Maximum continuation budget for developer agent"
        value={config.max_dev_continuations}
      />
      <ConfigField
        label="review_depth"
        description="Depth of reviewer analysis (shallow | standard | deep)"
        value={config.review_depth}
      />
      <ConfigField
        label="checkpoint_enabled"
        description="Persist pipeline state for --resume support"
        value={config.checkpoint_enabled}
      />
      <ConfigField
        label="isolation_mode"
        description="Run agents in isolated environment"
        value={config.isolation_mode}
      />
      <ConfigField
        label="interactive"
        description="Pause for user confirmation between phases"
        value={config.interactive}
      />
    </div>
  );
}

interface TomlEditorProps {
  label: string;
  repoPath: string | null;
  scope: "global" | "project";
}

const DEFAULT_TOML_TEMPLATE = (label: string) =>
  `# ${label} configuration (TOML)\n# Edit values below and save.\n\n[defaults]\n`;

const VALIDATION_DEBOUNCE_MS = 300;

function TomlEditor({ label, repoPath, scope }: TomlEditorProps) {
  const dispatch = useAppDispatch();
  const [toml, setToml] = useState(DEFAULT_TOML_TEMPLATE(label));
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Tracks the last successfully fetched TOML so Revert can restore it.
  const savedTomlRef = useRef<string>(DEFAULT_TOML_TEMPLATE(label));

  useEffect(() => {
    setLoading(true);
    const fetchToml = async () => {
      try {
        let content: string;
        if (scope === "global") {
          content = await getRawGlobalConfigToml();
        } else if (repoPath) {
          content = await getRawProjectConfigToml(repoPath);
        } else {
          content = "";
        }
        const resolved = content.length > 0 ? content : DEFAULT_TOML_TEMPLATE(label);
        setToml(resolved);
        savedTomlRef.current = resolved;
      } catch {
        const fallback = DEFAULT_TOML_TEMPLATE(label);
        setToml(fallback);
        savedTomlRef.current = fallback;
      } finally {
        setLoading(false);
      }
    };
    void fetchToml();
  }, [scope, repoPath, label]);

  // Validate TOML on every change, debounced to avoid spamming the backend.
  useEffect(() => {
    if (debounceTimerRef.current !== null) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      void validateConfigToml(toml).then((error) => {
        setValidationError(error ?? null);
      });
    }, VALIDATION_DEBOUNCE_MS);
    return () => {
      if (debounceTimerRef.current !== null) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [toml]);

  const handleSave = async () => {
    if (scope === "project" && !repoPath) return;
    if (validationError !== null) return;
    setSaving(true);
    setSaveMsg(null);
    setSaveError(null);
    try {
      if (scope === "global") {
        await saveGlobalConfig(toml);
        void dispatch(fetchGlobalConfig());
      } else if (repoPath) {
        await saveProjectConfig(repoPath, toml);
        void dispatch(fetchEffectiveConfig(repoPath));
      }
      savedTomlRef.current = toml;
      setSaveMsg("Saved successfully.");
      dispatch(setDirty(false));
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleRevert = () => {
    setToml(savedTomlRef.current);
    setValidationError(null);
    dispatch(setDirty(false));
  };

  const scopeLabel = scope === "global" ? "global" : "project";
  const isSaveDisabled = saving || (scope === "project" && !repoPath) || validationError !== null;

  return (
    <div>
      {loading && (
        <div
          style={{
            padding: "8px 0",
            fontSize: 11,
            color: "var(--text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          Loading...
        </div>
      )}
      <textarea
        className="form-input input-mono"
        value={toml}
        onChange={(e) => {
          setToml(e.target.value);
          dispatch(setDirty(true));
        }}
        style={{
          minHeight: 220,
          padding: "12px 14px",
          resize: "vertical",
          lineHeight: 1.7,
          fontSize: 12,
          borderColor: validationError ? "var(--status-failed)" : undefined,
          background: "var(--bg-base, #0d0d0d)",
          color: "var(--text-primary)",
        }}
        spellCheck={false}
      />
      {/* Inline validation error */}
      {validationError && (
        <div
          data-testid="toml-validation-error"
          style={{
            marginTop: 6,
            padding: "6px 10px",
            background: "rgba(248, 81, 73, 0.08)",
            border: "1px solid rgba(248, 81, 73, 0.25)",
            borderRadius: "var(--radius-md)",
            fontSize: 11,
            color: "var(--status-failed)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {validationError}
        </div>
      )}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginTop: 10,
        }}
      >
        <button
          className="btn btn-primary"
          onClick={() => void handleSave()}
          disabled={isSaveDisabled}
        >
          {saving ? "Saving..." : "Save"}
        </button>
        <button
          className="btn btn-ghost"
          data-testid="revert-config-button"
          onClick={handleRevert}
          disabled={toml === savedTomlRef.current}
          title="Discard unsaved changes and reload from saved state"
        >
          Revert
        </button>
        {/* Scope badge — persistent label so users know which scope they are editing */}
        <span
          data-testid="scope-badge"
          className="chip-mono"
          style={{
            fontSize: 10,
            color: "var(--text-muted)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          {scopeLabel}
        </span>
        {saveMsg && (
          <span
            style={{
              fontSize: 12,
              color: "var(--status-completed)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {saveMsg}
          </span>
        )}
        {saveError && (
          <span
            style={{
              fontSize: 12,
              color: "var(--status-failed)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {saveError}
          </span>
        )}
      </div>
    </div>
  );
}

/// AI Integration section shown in the Global tab.
/// Allows users to persist their Anthropic API key without touching CLI environment variables.
function AiIntegrationSection() {
  const dispatch = useAppDispatch();
  const aiApiKey = useAppSelector((s) => s.config.aiApiKey);
  const saveStatus = useAppSelector((s) => s.config.aiApiKeySaveStatus);
  const aiApiKeyError = useAppSelector((s) => s.config.aiApiKeyError);

  const [localKey, setLocalKey] = useState("");
  const [showKey, setShowKey] = useState(false);

  // Load saved key on mount
  useEffect(() => {
    void dispatch(fetchAiApiKey());
  }, [dispatch]);

  // Sync local input when the store key changes (e.g., loaded from backend)
  useEffect(() => {
    setLocalKey(aiApiKey);
  }, [aiApiKey]);

  // Auto-reset save status after 3 seconds so the success message fades
  useEffect(() => {
    if (saveStatus === "saved") {
      const timer = setTimeout(() => {
        dispatch(resetAiApiKeySaveStatus());
      }, 3000);
      return () => {
        clearTimeout(timer);
      };
    }
    return undefined;
  }, [dispatch, saveStatus]);

  const handleSave = () => {
    void dispatch(saveAiApiKeyThunk(localKey));
  };

  return (
    <div
      style={{
        marginTop: "var(--space-6)",
        paddingTop: "var(--space-6)",
        borderTop: "1px solid var(--border-subtle)",
      }}
    >
      {/* Section heading */}
      <div
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 13,
          fontWeight: 600,
          color: "var(--text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          marginBottom: "var(--space-3)",
        }}
      >
        AI Integration
      </div>

      <div
        style={{
          fontSize: 11,
          color: "var(--text-muted)",
          fontFamily: "var(--font-mono)",
          marginBottom: "var(--space-4)",
        }}
      >
        Anthropic API key used for AI-assisted PROMPT.md review. Stored at{" "}
        <span style={{ color: "var(--text-secondary)" }}>~/.config/ralph-gui.toml</span>{" "}
        with restricted permissions (0600).
      </div>

      {/* Key input with show/hide toggle */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <input
          type={showKey ? "text" : "password"}
          className="form-input input-mono"
          value={localKey}
          onChange={(e) => {
            setLocalKey(e.target.value);
          }}
          placeholder="Enter Anthropic API key (sk-ant-...)"
          style={{ flex: 1 }}
        />
        <button
          className="btn btn-ghost"
          onClick={() => {
            setShowKey((prev) => !prev);
          }}
          style={{ flexShrink: 0, minWidth: 52 }}
        >
          {showKey ? "Hide" : "Show"}
        </button>
      </div>

      {/* Save button + feedback */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-3)",
        }}
      >
        <button
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saveStatus === "saving"}
        >
          {saveStatus === "saving" ? "Saving..." : "Save API Key"}
        </button>
        {saveStatus === "saved" && (
          <span
            style={{
              fontSize: 12,
              color: "var(--status-completed)",
              fontFamily: "var(--font-mono)",
            }}
          >
            Saved
          </span>
        )}
        {saveStatus === "failed" && aiApiKeyError && (
          <span
            style={{
              fontSize: 12,
              color: "var(--status-failed)",
              fontFamily: "var(--font-mono)",
            }}
          >
            {aiApiKeyError}
          </span>
        )}
      </div>
    </div>
  );
}

export function Configuration() {
  const dispatch = useAppDispatch();
  const globalConfig = useAppSelector((s) => s.config.globalConfig);
  const effectiveConfig = useAppSelector((s) => s.config.effectiveConfig);
  const globalStatus = useAppSelector((s) => s.config.globalStatus);
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);
  const mainWorktree = worktrees.find((wt) => wt.is_main);
  const repoPath = activePath ?? mainWorktree?.path ?? null;

  const [activeTab, setActiveTab] = useState<TabId>("effective");

  useEffect(() => {
    void dispatch(fetchGlobalConfig());
  }, [dispatch]);

  useEffect(() => {
    if (repoPath) {
      void dispatch(fetchEffectiveConfig(repoPath));
    }
  }, [dispatch, repoPath]);

  const tabs: { id: TabId; label: string }[] = [
    { id: "effective", label: "Effective" },
    { id: "global", label: "Global" },
    { id: "project", label: "Project" },
  ];

  const displayConfig: ConfigView | null =
    activeTab === "effective" ? (effectiveConfig ?? globalConfig) : null;

  return (
    <div className="page-content">
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-6)",
          animation: "fadeIn 200ms ease",
        }}
      >
        <h1 className="page-title" style={{ marginBottom: 0 }}>
          Configuration
        </h1>
        {repoPath && (
          <span className="chip-mono" style={{ fontSize: 11 }}>
            {repoPath}
          </span>
        )}
      </div>

      <div style={{ animation: "fadeIn 200ms ease 40ms both" }}>
        {/* Tab bar */}
        <div className="tab-bar" style={{ marginBottom: "var(--space-5)" }}>
          {tabs.map((tab) => (
            <button
              key={tab.id}
              className={`tab-item${activeTab === tab.id ? " tab-item--active" : ""}`}
              onClick={() => {
                setActiveTab(tab.id);
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Effective read view */}
        {activeTab === "effective" && (
          <div className="card">
            <div
              style={{
                marginBottom: 12,
                fontSize: 11,
                color: "var(--text-muted)",
                fontFamily: "var(--font-mono)",
              }}
            >
              Merged view: project overrides global defaults
            </div>

            {globalStatus === "loading" && (
              <div
                style={{
                  padding: "var(--space-6)",
                  textAlign: "center",
                  color: "var(--text-muted)",
                  fontSize: 13,
                  fontFamily: "var(--font-mono)",
                }}
              >
                Loading...
              </div>
            )}

            {displayConfig ? (
              <ConfigTable config={displayConfig} />
            ) : globalStatus !== "loading" ? (
              <div
                style={{
                  padding: "var(--space-6)",
                  textAlign: "center",
                  color: "var(--text-muted)",
                  fontSize: 13,
                  fontFamily: "var(--font-mono)",
                }}
              >
                {!repoPath
                  ? "Select a repository to see effective config."
                  : "No configuration loaded."}
              </div>
            ) : null}
          </div>
        )}

        {/* Global edit view */}
        {activeTab === "global" && (
          <div className="card">
            <div
              style={{
                marginBottom: 16,
                fontSize: 11,
                color: "var(--text-muted)",
                fontFamily: "var(--font-mono)",
              }}
            >
              Global config stored at{" "}
              <span style={{ color: "var(--text-secondary)" }}>
                ~/.config/ralph-workflow.toml
              </span>
            </div>
            <TomlEditor label="Global" repoPath={null} scope="global" />
            <AiIntegrationSection />
          </div>
        )}

        {/* Project edit view */}
        {activeTab === "project" && (
          <div className="card">
            <div
              style={{
                marginBottom: 16,
                fontSize: 11,
                color: "var(--text-muted)",
                fontFamily: "var(--font-mono)",
              }}
            >
              Project-level config stored at{" "}
              <span style={{ color: "var(--text-secondary)" }}>
                {repoPath ? `${repoPath}/.ralph/config.toml` : "<repo>/.ralph/config.toml"}
              </span>
            </div>
            {!repoPath && (
              <div
                style={{
                  padding: "10px 12px",
                  background: "rgba(232,168,56,0.08)",
                  border: "1px solid rgba(232,168,56,0.2)",
                  borderRadius: "var(--radius-md)",
                  color: "var(--accent)",
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  marginBottom: 12,
                }}
              >
                Select a repository context to edit project config.
              </div>
            )}
            <TomlEditor label="Project" repoPath={repoPath} scope="project" />
          </div>
        )}
      </div>
    </div>
  );
}
