import { useState, useEffect } from "react";
import { useAppSelector } from "../../store";
import { listAgentProfiles, launchRalphSession, savePromptFile } from "../../api/tauri";
import type { AgentProfile } from "../../types";
import { PreflightSummary } from "./PreflightSummary";
import { PromptTemplatePicker } from "./PromptTemplatePicker";
import { PROMPT_TEMPLATES } from "./promptTemplates";
import { PromptReviewPanel } from "./PromptReviewPanel";

const PRESETS_KEY = "ralph_gui_presets";

interface LaunchPreset {
  name: string;
  developerIterations: number;
  reviewerPasses: number;
  agentProfile: string | null;
}

function loadPresets(): LaunchPreset[] {
  try {
    const raw = localStorage.getItem(PRESETS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as LaunchPreset[];
  } catch {
    return [];
  }
}

function savePresets(presets: LaunchPreset[]): void {
  localStorage.setItem(PRESETS_KEY, JSON.stringify(presets));
}

type WizardStep = "template" | "config" | "preflight";

interface NewSessionWizardProps {
  onClose: () => void;
  preselectedWorktreePath?: string | null;
}

export function NewSessionWizard({
  onClose,
  preselectedWorktreePath,
}: NewSessionWizardProps) {
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);

  const [step, setStep] = useState<WizardStep>("template");
  const [repoPath, setRepoPath] = useState(
    worktrees.find((wt) => wt.is_main)?.path ?? "",
  );
  const [worktreePath, setWorktreePath] = useState<string | null>(
    preselectedWorktreePath !== undefined ? preselectedWorktreePath : activePath,
  );
  const [promptContent, setPromptContent] = useState(
    PROMPT_TEMPLATES.find((t) => t.id === "feature")?.content ?? "",
  );
  const [developerIterations, setDeveloperIterations] = useState(5);
  const [reviewerPasses, setReviewerPasses] = useState(2);
  const [isLaunching, setIsLaunching] = useState(false);
  const [showReviewPanel, setShowReviewPanel] = useState(false);
  const [agentProfiles, setAgentProfiles] = useState<AgentProfile[]>([]);
  const [selectedAgentProfile, setSelectedAgentProfile] = useState<
    string | null
  >(null);

  // Preset state
  const [presets, setPresets] = useState<LaunchPreset[]>(() => loadPresets());
  const [presetNameInput, setPresetNameInput] = useState("");

  // Load agent profiles when repo path is known
  useEffect(() => {
    if (!repoPath) return;
    listAgentProfiles(repoPath)
      .then((profiles) => {
        setAgentProfiles(profiles);
      })
      .catch(() => {
        // Non-fatal: agent profiles are optional
        setAgentProfiles([]);
      });
  }, [repoPath]);

  const promptPath = repoPath ? `${repoPath}/PROMPT.md` : "";

  function handleSavePreset() {
    if (!presetNameInput.trim()) return;
    const updated = [
      ...presets.filter((p) => p.name !== presetNameInput.trim()),
      {
        name: presetNameInput.trim(),
        developerIterations,
        reviewerPasses,
        agentProfile: selectedAgentProfile,
      },
    ];
    savePresets(updated);
    setPresets(updated);
    setPresetNameInput("");
  }

  function handleLoadPreset(name: string) {
    const preset = presets.find((p) => p.name === name);
    if (!preset) return;
    setDeveloperIterations(preset.developerIterations);
    setReviewerPasses(preset.reviewerPasses);
    setSelectedAgentProfile(preset.agentProfile);
  }

  function handleDeletePreset(name: string) {
    const updated = presets.filter((p) => p.name !== name);
    savePresets(updated);
    setPresets(updated);
  }

  function handleTemplateSelect(content: string) {
    setPromptContent(content);
    setStep("config");
  }

  function handleNext() {
    if (!repoPath.trim()) return;
    setStep("preflight");
  }

  async function handleLaunch() {
    setIsLaunching(true);
    try {
      // Write the prompt to disk before launching
      if (promptPath) {
        await savePromptFile(promptPath, promptContent);
      }
      const selectedProfile = agentProfiles.find(
        (p) => p.name === selectedAgentProfile,
      );
      await launchRalphSession({
        repo_path: repoPath,
        worktree_path: worktreePath,
        prompt_path: promptPath,
        developer_iterations: developerIterations,
        reviewer_passes: reviewerPasses,
        developer_agent: selectedProfile?.developer_agent ?? null,
        reviewer_agent: selectedProfile?.reviewer_agent ?? null,
      });
      onClose();
    } finally {
      setIsLaunching(false);
    }
  }

  if (step === "template") {
    return (
      <div
        data-testid="wizard-template-step"
        style={{ display: "flex", flexDirection: "column", gap: 20 }}
      >
        <PromptTemplatePicker onSelect={handleTemplateSelect} />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (step === "preflight") {
    return (
      <PreflightSummary
        repoPath={repoPath}
        worktreePath={worktreePath}
        promptPath={promptPath}
        developerIterations={developerIterations}
        reviewerPasses={reviewerPasses}
        onConfirm={() => void handleLaunch()}
        onBack={() => setStep("config")}
        isLaunching={isLaunching}
      />
    );
  }

  // Step: config
  return (
    <div
      data-testid="wizard-config-step"
      style={{ display: "flex", flexDirection: "column", gap: 16 }}
    >
      {/* Repo path */}
      <div>
        <label className="section-label" htmlFor="repo-path">
          Repository path
        </label>
        <input
          id="repo-path"
          className="input input-mono"
          value={repoPath}
          onChange={(e) => setRepoPath(e.target.value)}
          placeholder="/path/to/your/repo"
        />
      </div>

      {/* Worktree selection */}
      {worktrees.length > 0 && (
        <div>
          <label className="section-label" htmlFor="worktree-select">
            Context
          </label>
          <select
            id="worktree-select"
            className="input"
            value={worktreePath ?? ""}
            onChange={(e) => setWorktreePath(e.target.value || null)}
            style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
          >
            <option value="">Direct repository (no worktree)</option>
            {worktrees
              .filter((wt) => !wt.is_main)
              .map((wt) => (
                <option key={wt.path} value={wt.path}>
                  {wt.name} ({wt.branch})
                </option>
              ))}
          </select>
        </div>
      )}

      {/* Agent profile selection */}
      {agentProfiles.length > 0 && (
        <div>
          <label className="section-label" htmlFor="agent-profile">
            Agent profile
          </label>
          <select
            id="agent-profile"
            data-testid="agent-profile-select"
            className="input"
            value={selectedAgentProfile ?? ""}
            onChange={(e) => setSelectedAgentProfile(e.target.value || null)}
            style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
          >
            <option value="">Default (from config)</option>
            {agentProfiles.map((p) => (
              <option key={p.name} value={p.name}>
                {p.name} — dev: {p.developer_agent} / reviewer:{" "}
                {p.reviewer_agent}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* Preset picker */}
      {presets.length > 0 && (
        <div>
          <label className="section-label">Load preset</label>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <select
              data-testid="load-preset-select"
              className="input"
              defaultValue=""
              onChange={(e) => {
                if (e.target.value) handleLoadPreset(e.target.value);
              }}
              style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
            >
              <option value="">— select a preset —</option>
              {presets.map((p) => (
                <option key={p.name} value={p.name}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
          {/* Saved preset list with delete buttons */}
          <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {presets.map((p) => (
              <span
                key={p.name}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 8px",
                  borderRadius: 100,
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-subtle)",
                  fontSize: 11,
                  color: "var(--text-secondary)",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {p.name}
                <button
                  data-testid={`delete-preset-${p.name}`}
                  onClick={() => handleDeletePreset(p.name)}
                  style={{
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text-muted)",
                    fontSize: 10,
                    padding: 0,
                    lineHeight: 1,
                  }}
                  title={`Delete preset "${p.name}"`}
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Iteration config */}
      <div
        style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}
      >
        <div>
          <label className="section-label" htmlFor="dev-iters">
            Dev iterations
          </label>
          <input
            id="dev-iters"
            className="input"
            type="number"
            min={1}
            max={20}
            value={developerIterations}
            onChange={(e) => setDeveloperIterations(Number(e.target.value))}
          />
        </div>
        <div>
          <label className="section-label" htmlFor="rev-passes">
            Review passes
          </label>
          <input
            id="rev-passes"
            className="input"
            type="number"
            min={1}
            max={10}
            value={reviewerPasses}
            onChange={(e) => setReviewerPasses(Number(e.target.value))}
          />
        </div>
      </div>

      {/* Save as preset */}
      <div>
        <label className="section-label">Save current config as preset</label>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            data-testid="preset-name-input"
            className="input input-mono"
            value={presetNameInput}
            onChange={(e) => setPresetNameInput(e.target.value)}
            placeholder="Preset name…"
            style={{ flex: 1 }}
          />
          <button
            data-testid="save-preset-button"
            className="btn btn-secondary"
            onClick={handleSavePreset}
            disabled={!presetNameInput.trim()}
            style={{ flexShrink: 0 }}
          >
            Save
          </button>
        </div>
      </div>

      {/* Prompt editor with optional AI review panel */}
      <div>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 6,
          }}
        >
          <label className="section-label" style={{ marginBottom: 0 }}>
            PROMPT.md
          </label>
          <button
            className="btn btn-ghost"
            style={{ fontSize: 11, padding: "2px 10px" }}
            onClick={() => setShowReviewPanel((v) => !v)}
            data-testid="toggle-review-panel"
          >
            {showReviewPanel ? "Hide AI review" : "Review with AI"}
          </button>
        </div>
        <textarea
          id="prompt-editor"
          className="input input-mono"
          rows={10}
          value={promptContent}
          onChange={(e) => setPromptContent(e.target.value)}
          style={{ resize: "vertical", lineHeight: 1.6 }}
        />
        {showReviewPanel && (
          <div style={{ marginTop: 10 }}>
            <PromptReviewPanel
              promptContent={promptContent}
              onApplyImprovedPrompt={(improved) => setPromptContent(improved)}
            />
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button
          className="btn btn-ghost"
          onClick={() => setStep("template")}
        >
          ← Templates
        </button>
        <button className="btn btn-ghost" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          onClick={handleNext}
          disabled={!repoPath.trim()}
          data-testid="review-launch-button"
        >
          Review & launch →
        </button>
      </div>
    </div>
  );
}
