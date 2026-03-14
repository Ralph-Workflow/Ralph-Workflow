import { describe, it, expect, vi } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { TAURI_INVOKE } from '../../../services/tauri.service';
import { AddAgentDialogComponent } from './add-agent-dialog.component';
import type { AgentToolInfo } from '../../../types';

// ── Helpers ──────────────────────────────────────────────────────────────────

const MOCK_TOOL_CLAUDE: AgentToolInfo = {
  name: 'Claude Code',
  binary: 'claude',
  installed: true,
  version: '1.0.0',
  auth_status: 'Ready',
  health: 'Ready',
  description: 'Claude AI coding assistant',
  available_models: ['claude-opus-4-5', 'claude-sonnet-4-6'],
  binary_location: '/usr/bin/claude',
};

const MOCK_TOOL_CODEX: AgentToolInfo = {
  name: 'Codex',
  binary: 'codex',
  installed: true,
  version: '0.1.0',
  auth_status: 'Ready',
  health: 'Ready',
  description: 'OpenAI Codex CLI',
  available_models: ['gpt-4o', 'gpt-4o-mini'],
  binary_location: '/usr/bin/codex',
};

const MOCK_TOOL_UNINSTALLED: AgentToolInfo = {
  name: 'OpenCode',
  binary: 'opencode',
  installed: false,
  version: null,
  auth_status: 'N/A',
  health: 'Not installed',
  description: 'Open-source agent',
  available_models: [],
  binary_location: null,
};

function makeDialogRef(): Partial<MatDialogRef<AddAgentDialogComponent>> {
  return {
    close: vi.fn(),
  };
}

function makeInvoke(tools: AgentToolInfo[] = [MOCK_TOOL_CLAUDE, MOCK_TOOL_CODEX, MOCK_TOOL_UNINSTALLED]) {
  return (cmd: string) => {
    if (cmd === 'get_agent_tools') return Promise.resolve(tools);
    if (cmd === 'refresh_tool_models') return Promise.resolve(['model-a', 'model-b']);
    return Promise.resolve(null);
  };
}

async function createDialog(
  dialogData: { agentName?: string; existingNames?: string[] } = {},
  tools: AgentToolInfo[] = [MOCK_TOOL_CLAUDE, MOCK_TOOL_CODEX, MOCK_TOOL_UNINSTALLED],
) {
  const dialogRef = makeDialogRef();
  const mockInvoke = vi.fn().mockImplementation(makeInvoke(tools));

  await TestBed.configureTestingModule({
    imports: [AddAgentDialogComponent],
    providers: [
      provideZonelessChangeDetection(),
      provideAnimationsAsync(),
      { provide: MatDialogRef, useValue: dialogRef },
      { provide: MAT_DIALOG_DATA, useValue: dialogData },
      { provide: TAURI_INVOKE, useValue: mockInvoke },
    ],
  }).compileComponents();

  const fixture = TestBed.createComponent(AddAgentDialogComponent);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();

  return { fixture, dialogRef, mockInvoke };
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('AddAgentDialogComponent', () => {
  it('should create', async () => {
    const { fixture } = await createDialog();
    expect(fixture.componentInstance).toBeTruthy();
  });

  it('should render agent name text input', async () => {
    const { fixture } = await createDialog();
    const input: HTMLInputElement | null = fixture.nativeElement.querySelector('input[data-testid="agent-name"]');
    expect(input).not.toBeNull();
  });

  it('should show radio buttons for installed CLI tools only', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    // Only installed tools (Claude Code, Codex) should appear; OpenCode is not installed
    const radios: NodeListOf<HTMLInputElement> = fixture.nativeElement.querySelectorAll('input[type="radio"][name="tool"]');
    expect(radios.length).toBe(2);
  });

  it('should filter out uninstalled tools', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).not.toContain('OpenCode');
  });

  it('should disable submit button when agent name is empty', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button[data-testid="submit-btn"]');
    expect(btn?.disabled).toBe(true);
  });

  it('should disable submit button when no tool is selected', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.agentName.set('my-agent');
    fixture.detectChanges();
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button[data-testid="submit-btn"]');
    expect(btn?.disabled).toBe(true);
  });

  it('should enable submit button when name and tool are both set', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.agentName.set('my-agent');
    comp.selectedTool.set(MOCK_TOOL_CLAUDE.name);
    comp.selectedModel.set(MOCK_TOOL_CLAUDE.available_models[0] ?? 'claude-opus-4-5');
    fixture.detectChanges();
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button[data-testid="submit-btn"]');
    expect(btn?.disabled).toBe(false);
  });

  it('should close dialog without emitting when cancel is clicked', async () => {
    const { fixture, dialogRef } = await createDialog();
    await fixture.whenStable();
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button[data-testid="cancel-btn"]');
    btn?.click();
    expect(dialogRef.close).toHaveBeenCalledWith(null);
  });

  it('should close dialog with AgentDefinition when form is submitted', async () => {
    const { fixture, dialogRef } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.agentName.set('my-agent');
    comp.selectedTool.set(MOCK_TOOL_CLAUDE.name);
    comp.selectedModel.set(MOCK_TOOL_CLAUDE.available_models[0] ?? 'claude-opus-4-5');
    fixture.detectChanges();
    const btn: HTMLButtonElement | null = fixture.nativeElement.querySelector('button[data-testid="submit-btn"]');
    btn?.click();
    expect(dialogRef.close).toHaveBeenCalledWith({
      name: 'my-agent',
      tool: 'Claude Code',
      model: 'claude-opus-4-5',
    });
  });

  it('should show validation error for duplicate agent name', async () => {
    const { fixture } = await createDialog({ existingNames: ['existing-agent'] });
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.agentName.set('existing-agent');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('already exists');
  });

  it('should auto-select tool when only one tool is installed', async () => {
    const singleTool = [MOCK_TOOL_CLAUDE, MOCK_TOOL_UNINSTALLED];
    const { fixture } = await createDialog({}, singleTool);
    await fixture.whenStable();
    fixture.detectChanges();
    // With only one installed tool, it should be auto-selected
    expect(fixture.componentInstance.selectedTool()).toBe('Claude Code');
  });

  it('should show empty state with message when no tools installed', async () => {
    const noTools = [MOCK_TOOL_UNINSTALLED];
    const { fixture } = await createDialog({}, noTools);
    await fixture.whenStable();
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    // Check case-insensitively for "no installed" message
    expect(el.textContent?.toLowerCase()).toContain('no installed');
  });

  it('should pre-fill agent name in edit mode', async () => {
    const { fixture } = await createDialog({ agentName: 'edit-this-agent' });
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    expect(comp.agentName()).toBe('edit-this-agent');
  });

  it('should show model selection after tool is selected', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.selectedTool.set('Claude Code');
    fixture.detectChanges();
    await fixture.whenStable();
    // Model selection should appear
    const modelSection = fixture.nativeElement.querySelector('[data-testid="model-section"]');
    expect(modelSection).not.toBeNull();
  });

  it('should use searchable combo when model list has more than 15 items', async () => {
    const manyModels = Array.from({ length: 20 }, (_, i) => `model-${i}`);
    const bigTool: AgentToolInfo = { ...MOCK_TOOL_CLAUDE, available_models: manyModels };
    const { fixture } = await createDialog({}, [bigTool]);
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.selectedTool.set('Claude Code');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    // Searchable combo = text input for model search
    const searchInput = fixture.nativeElement.querySelector('[data-testid="model-search"]');
    expect(searchInput).not.toBeNull();
  });

  it('should use dropdown when model list has 15 or fewer items', async () => {
    const { fixture } = await createDialog();
    await fixture.whenStable();
    fixture.detectChanges();
    const comp = fixture.componentInstance;
    comp.selectedTool.set('Claude Code');
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    // Small list = mat-select dropdown
    const dropdown = fixture.nativeElement.querySelector('[data-testid="model-dropdown"]');
    expect(dropdown).not.toBeNull();
  });
});
