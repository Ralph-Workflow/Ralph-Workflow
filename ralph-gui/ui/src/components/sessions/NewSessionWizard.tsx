import { useState } from "react";
import { useAppDispatch, useAppSelector } from "../../store";
import { createNewSession } from "../../store/slices/sessionSlice";
import { PreflightSummary } from "./PreflightSummary";

type WizardStep = "config" | "preflight";

interface NewSessionWizardProps {
  onClose: () => void;
}

const PROMPT_TEMPLATE = `# Task Specification

## Goal
<!-- Describe what you want Ralph to build or fix -->

## Acceptance Criteria
<!-- List specific, testable criteria that must pass -->
- [ ]

## Context
<!-- Repository, relevant files, constraints, patterns to follow -->

## Out of Scope
<!-- What should NOT be changed -->
`;

export function NewSessionWizard({ onClose }: NewSessionWizardProps) {
  const dispatch = useAppDispatch();
  const worktrees = useAppSelector((s) => s.worktrees.worktrees);
  const activePath = useAppSelector((s) => s.worktrees.activeWorktreePath);

  const [step, setStep] = useState<WizardStep>("config");
  const [repoPath, setRepoPath] = useState(
    worktrees.find((wt) => wt.is_main)?.path ?? "",
  );
  const [worktreePath, setWorktreePath] = useState<string | null>(activePath);
  const [promptContent, setPromptContent] = useState(PROMPT_TEMPLATE);
  const [developerIterations, setDeveloperIterations] = useState(5);
  const [reviewerPasses, setReviewerPasses] = useState(2);
  const [isLaunching, setIsLaunching] = useState(false);

  // For simplicity, save prompt to a temp path in the UI
  const promptPath = repoPath ? `${repoPath}/PROMPT.md` : "";

  function handleNext() {
    if (!repoPath.trim()) return;
    setStep("preflight");
  }

  async function handleLaunch() {
    setIsLaunching(true);
    try {
      await dispatch(
        createNewSession({
          repo_path: repoPath,
          worktree_path: worktreePath,
          prompt_path: promptPath,
          developer_iterations: developerIterations,
          reviewer_passes: reviewerPasses,
        }),
      );
      onClose();
    } finally {
      setIsLaunching(false);
    }
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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Repo path */}
      <div>
        <label className="section-label" htmlFor="repo-path">Repository path</label>
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
          <label className="section-label" htmlFor="worktree-select">Context</label>
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

      {/* Iteration config */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div>
          <label className="section-label" htmlFor="dev-iters">Dev iterations</label>
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
          <label className="section-label" htmlFor="rev-passes">Review passes</label>
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

      {/* Prompt editor */}
      <div>
        <label className="section-label" htmlFor="prompt-editor">PROMPT.md</label>
        <textarea
          id="prompt-editor"
          className="input input-mono"
          rows={10}
          value={promptContent}
          onChange={(e) => setPromptContent(e.target.value)}
          style={{ resize: "vertical", lineHeight: 1.6 }}
        />
      </div>

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-ghost" onClick={onClose}>
          Cancel
        </button>
        <button
          className="btn btn-primary"
          onClick={handleNext}
          disabled={!repoPath.trim()}
        >
          Review & launch →
        </button>
      </div>
    </div>
  );
}
