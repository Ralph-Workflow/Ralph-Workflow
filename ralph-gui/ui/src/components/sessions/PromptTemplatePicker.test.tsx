import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PromptTemplatePicker } from "./PromptTemplatePicker";
import { PROMPT_TEMPLATES } from "./promptTemplates";

describe("PromptTemplatePicker", () => {
  it("renders all templates", () => {
    const onSelect = vi.fn();
    render(<PromptTemplatePicker onSelect={onSelect} />);
    for (const tpl of PROMPT_TEMPLATES) {
      expect(screen.getByTestId(`template-${tpl.id}`)).toBeInTheDocument();
      expect(screen.getByText(tpl.label)).toBeInTheDocument();
    }
  });

  it("fires onSelect with correct content when a template is clicked", () => {
    const onSelect = vi.fn();
    render(<PromptTemplatePicker onSelect={onSelect} />);
    const featureBtn = screen.getByTestId("template-feature");
    fireEvent.click(featureBtn);
    const featureTpl = PROMPT_TEMPLATES.find((t) => t.id === "feature");
    if (!featureTpl) throw new Error("feature template not found");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(featureTpl.content);
  });

  it("fires onSelect with blank content for blank template", () => {
    const onSelect = vi.fn();
    render(<PromptTemplatePicker onSelect={onSelect} />);
    const blankBtn = screen.getByTestId("template-blank");
    fireEvent.click(blankBtn);
    const blankTpl = PROMPT_TEMPLATES.find((t) => t.id === "blank");
    if (!blankTpl) throw new Error("blank template not found");
    expect(onSelect).toHaveBeenCalledWith(blankTpl.content);
  });

  it("has 5 templates (feature, bugfix, refactor, test-coverage, blank)", () => {
    expect(PROMPT_TEMPLATES).toHaveLength(5);
    const ids = PROMPT_TEMPLATES.map((t) => t.id);
    expect(ids).toContain("feature");
    expect(ids).toContain("bugfix");
    expect(ids).toContain("refactor");
    expect(ids).toContain("test-coverage");
    expect(ids).toContain("blank");
  });

  it("each template has acceptance criteria and out-of-scope sections for quality unattended sessions", () => {
    for (const tpl of PROMPT_TEMPLATES) {
      expect(tpl.content).toMatch(/## Acceptance Criteria/i);
      expect(tpl.content).toMatch(/## Out of Scope/i);
    }
  });
});
