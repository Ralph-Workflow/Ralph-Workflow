import { useEffect, useState } from "react";
import { useAppSelector, useAppDispatch } from "../store";
import {
  fetchGlobalConfig,
  fetchEffectiveConfig,
  setDirty,
} from "../store/slices/configSlice";
import { saveGlobalConfig, saveProjectConfig } from "../api/tauri";
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

function TomlEditor({ label, repoPath, scope }: TomlEditorProps) {
  const dispatch = useAppDispatch();
  const [toml, setToml] = useState(
    `# ${label} configuration (TOML)\n# Edit values below and save.\n\n[defaults]\n`,
  );
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const handleSave = async () => {
    if (scope === "project" && !repoPath) return;
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
      setSaveMsg("Saved successfully.");
      dispatch(setDirty(false));
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <textarea
        value={toml}
        onChange={(e) => {
          setToml(e.target.value);
          dispatch(setDirty(true));
        }}
        style={{
          width: "100%",
          minHeight: 220,
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-md)",
          padding: "12px",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          color: "var(--text-primary)",
          resize: "vertical",
          outline: "none",
          lineHeight: 1.6,
        }}
        spellCheck={false}
      />
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
          disabled={saving || (scope === "project" && !repoPath)}
        >
          {saving ? "Saving..." : "Save"}
        </button>
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
              onClick={() => setActiveTab(tab.id)}
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
