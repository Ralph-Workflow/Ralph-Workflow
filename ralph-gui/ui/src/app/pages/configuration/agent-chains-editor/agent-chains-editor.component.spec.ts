import { describe, it, expect, beforeEach } from 'vitest';
import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { CdkDragDrop } from '@angular/cdk/drag-drop';
import { TAURI_INVOKE } from '../../../services/tauri.service';
import { AgentChainsEditorComponent } from './agent-chains-editor.component';
import {
  parseAgentChainsConfig,
  serializeAgentChainsConfig,
  parseAgentDefinitions,
  serializeAgentDefinition,
  removeAgentDefinition,
} from './agent-chains-editor.utils';
import type { AgentChainsConfig, AgentDefinitionEntry } from './agent-chains-editor.types';

// ─── Unit tests for pure parsing/serialization utils ───────────────────────

describe('parseAgentChainsConfig', () => {
  it('should parse empty TOML as empty chains and drains', () => {
    const result = parseAgentChainsConfig('');
    expect(result.chains).toEqual({});
    expect(result.drains).toEqual({});
  });

  it('should parse a single chain', () => {
    const toml = `
[agent_chains]
developer = ["claude-code"]
`;
    const result = parseAgentChainsConfig(toml);
    expect(result.chains['developer']).toEqual(['claude-code']);
  });

  it('should parse multiple chains', () => {
    const toml = `
[agent_chains]
developer = ["claude-code", "codex"]
reviewer = ["claude-opus"]
`;
    const result = parseAgentChainsConfig(toml);
    expect(result.chains['developer']).toEqual(['claude-code', 'codex']);
    expect(result.chains['reviewer']).toEqual(['claude-opus']);
  });

  it('should parse drain bindings', () => {
    const toml = `
[agent_drains]
development = "developer"
review = "reviewer"
`;
    const result = parseAgentChainsConfig(toml);
    expect(result.drains['development']).toBe('developer');
    expect(result.drains['review']).toBe('reviewer');
  });

  it('should parse both chains and drains', () => {
    const toml = `
[agent_chains]
developer = ["claude-code"]

[agent_drains]
development = "developer"
planning = "developer"
`;
    const result = parseAgentChainsConfig(toml);
    expect(result.chains['developer']).toEqual(['claude-code']);
    expect(result.drains['development']).toBe('developer');
    expect(result.drains['planning']).toBe('developer');
  });

  it('should return empty chains/drains for TOML with no relevant sections', () => {
    const toml = `
[defaults]
verbosity = 2
developer_iters = 3
`;
    const result = parseAgentChainsConfig(toml);
    expect(result.chains).toEqual({});
    expect(result.drains).toEqual({});
  });
});

describe('serializeAgentChainsConfig', () => {
  it('should serialize empty config as empty string', () => {
    const config: AgentChainsConfig = { chains: {}, drains: {} };
    const result = serializeAgentChainsConfig(config, '');
    expect(result).toBe('');
  });

  it('should serialize a single chain into the existing TOML', () => {
    const config: AgentChainsConfig = {
      chains: { developer: ['claude-code'] },
      drains: {},
    };
    const existing = '[defaults]\nverbosity = 2\n';
    const result = serializeAgentChainsConfig(config, existing);
    expect(result).toContain('[agent_chains]');
    expect(result).toContain('developer = ["claude-code"]');
    // Preserves existing content
    expect(result).toContain('[defaults]');
    expect(result).toContain('verbosity = 2');
  });

  it('should serialize drain bindings', () => {
    const config: AgentChainsConfig = {
      chains: {},
      drains: { development: 'developer', planning: 'developer' },
    };
    const result = serializeAgentChainsConfig(config, '');
    expect(result).toContain('[agent_drains]');
    expect(result).toContain('development = "developer"');
    expect(result).toContain('planning = "developer"');
  });

  it('should replace existing agent_chains section', () => {
    const config: AgentChainsConfig = {
      chains: { mychain: ['new-agent'] },
      drains: {},
    };
    const existing = '[agent_chains]\noldchain = ["old-agent"]\n';
    const result = serializeAgentChainsConfig(config, existing);
    expect(result).toContain('mychain = ["new-agent"]');
    expect(result).not.toContain('oldchain');
  });

  it('should replace existing agent_drains section', () => {
    const config: AgentChainsConfig = {
      chains: {},
      drains: { development: 'newchain' },
    };
    const existing = '[agent_drains]\ndevelopment = "oldchain"\n';
    const result = serializeAgentChainsConfig(config, existing);
    expect(result).toContain('development = "newchain"');
    expect(result).not.toContain('oldchain');
  });
});

// ─── Unit tests for agent definition parsing/serialization ──────────────────

describe('parseAgentDefinitions', () => {
  it('should return empty array for empty TOML', () => {
    const result = parseAgentDefinitions('');
    expect(result).toEqual([]);
  });

  it('should parse a single [agents.NAME] section', () => {
    const toml = `
[agents.my-agent]
tool = "claude-code"
model = "claude-opus-4"
`;
    const result = parseAgentDefinitions(toml);
    expect(result.length).toBe(1);
    expect(result[0]!.name).toBe('my-agent');
    expect(result[0]!.tool).toBe('claude-code');
    expect(result[0]!.model).toBe('claude-opus-4');
  });

  it('should parse multiple [agents.NAME] sections', () => {
    const toml = `
[agents.agent-a]
tool = "claude-code"
model = "model-a"

[agents.agent-b]
tool = "codex"
model = "model-b"
`;
    const result = parseAgentDefinitions(toml);
    expect(result.length).toBe(2);
    expect(result.find(a => a.name === 'agent-a')?.tool).toBe('claude-code');
    expect(result.find(a => a.name === 'agent-b')?.tool).toBe('codex');
  });

  it('should ignore non-agents sections like [agent_chains]', () => {
    const toml = `
[agent_chains]
developer = ["claude-code"]

[agents.my-agent]
tool = "claude-code"
model = "model-x"
`;
    const result = parseAgentDefinitions(toml);
    expect(result.length).toBe(1);
    expect(result[0]!.name).toBe('my-agent');
  });

  it('should return empty array when only non-agents sections present', () => {
    const toml = `
[defaults]
verbosity = 2

[agent_chains]
developer = ["claude-code"]
`;
    const result = parseAgentDefinitions(toml);
    expect(result).toEqual([]);
  });

  it('should handle agent section with only tool field', () => {
    const toml = `
[agents.minimal]
tool = "claude-code"
`;
    const result = parseAgentDefinitions(toml);
    expect(result.length).toBe(1);
    expect(result[0]!.name).toBe('minimal');
    expect(result[0]!.tool).toBe('claude-code');
    expect(result[0]!.model).toBe('');
  });
});

describe('serializeAgentDefinition', () => {
  it('should add a new [agents.NAME] section to existing TOML', () => {
    const agent: AgentDefinitionEntry = { name: 'my-agent', tool: 'claude-code', model: 'claude-opus-4' };
    const existing = '[defaults]\nverbosity = 2\n';
    const result = serializeAgentDefinition(agent, existing);
    expect(result).toContain('[agents.my-agent]');
    expect(result).toContain('tool = "claude-code"');
    expect(result).toContain('model = "claude-opus-4"');
    // Should preserve existing content
    expect(result).toContain('[defaults]');
    expect(result).toContain('verbosity = 2');
  });

  it('should replace an existing [agents.NAME] section when name matches', () => {
    const existing = '[agents.my-agent]\ntool = "old-tool"\nmodel = "old-model"\n';
    const agent: AgentDefinitionEntry = { name: 'my-agent', tool: 'new-tool', model: 'new-model' };
    const result = serializeAgentDefinition(agent, existing);
    expect(result).toContain('tool = "new-tool"');
    expect(result).toContain('model = "new-model"');
    expect(result).not.toContain('old-tool');
    expect(result).not.toContain('old-model');
  });

  it('should preserve other [agents.OTHER] sections when serializing a new agent', () => {
    const existing = '[agents.existing]\ntool = "codex"\nmodel = "existing-model"\n';
    const agent: AgentDefinitionEntry = { name: 'new-agent', tool: 'claude-code', model: 'new-model' };
    const result = serializeAgentDefinition(agent, existing);
    expect(result).toContain('[agents.existing]');
    expect(result).toContain('[agents.new-agent]');
  });

  it('should omit model line when model is empty', () => {
    const agent: AgentDefinitionEntry = { name: 'no-model', tool: 'claude-code', model: '' };
    const result = serializeAgentDefinition(agent, '');
    expect(result).toContain('[agents.no-model]');
    expect(result).toContain('tool = "claude-code"');
    expect(result).not.toContain('model =');
  });

  it('should produce round-trip stable output', () => {
    const agent: AgentDefinitionEntry = { name: 'round-trip', tool: 'codex', model: 'gpt-4' };
    const toml1 = serializeAgentDefinition(agent, '');
    const toml2 = serializeAgentDefinition(agent, toml1);
    // Second serialization should not duplicate the section
    const matchCount = (toml2.match(/\[agents\.round-trip\]/g) ?? []).length;
    expect(matchCount).toBe(1);
  });
});

describe('removeAgentDefinition', () => {
  it('should remove an existing [agents.NAME] section', () => {
    const existing = '[agents.my-agent]\ntool = "claude-code"\nmodel = "model-x"\n';
    const result = removeAgentDefinition('my-agent', existing);
    expect(result).not.toContain('[agents.my-agent]');
    expect(result).not.toContain('tool = "claude-code"');
  });

  it('should preserve other content when removing an agent section', () => {
    const existing = '[defaults]\nverbosity = 2\n\n[agents.my-agent]\ntool = "claude-code"\n\n[agent_chains]\ndeveloper = []\n';
    const result = removeAgentDefinition('my-agent', existing);
    expect(result).not.toContain('[agents.my-agent]');
    expect(result).toContain('[defaults]');
    expect(result).toContain('verbosity = 2');
    expect(result).toContain('[agent_chains]');
  });

  it('should return original TOML when agent name not found', () => {
    const existing = '[defaults]\nverbosity = 2\n';
    const result = removeAgentDefinition('nonexistent', existing);
    expect(result).toContain('[defaults]');
    expect(result).toContain('verbosity = 2');
  });

  it('should preserve other agents when removing one', () => {
    const existing = '[agents.agent-a]\ntool = "claude-code"\n\n[agents.agent-b]\ntool = "codex"\n';
    const result = removeAgentDefinition('agent-a', existing);
    expect(result).not.toContain('[agents.agent-a]');
    expect(result).toContain('[agents.agent-b]');
    expect(result).toContain('tool = "codex"');
  });
});

// ─── Component tests ─────────────────────────────────────────────────────

const mockInvoke = (cmd: string) => {
  if (cmd === 'get_agent_tools') return Promise.resolve([]);
  return Promise.resolve(null);
};

describe('AgentChainsEditorComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AgentChainsEditorComponent],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        provideAnimationsAsync(),
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();
  });

  it('should create', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const component = fixture.componentInstance;
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  it('should render the Chains section header', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Chains');
  });

  it('should render the Drains section header', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('Drains');
  });

  it('should render all 6 drain labels', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const text = el.textContent ?? '';
    expect(text).toContain('Planning');
    expect(text).toContain('Development');
    expect(text).toContain('Review');
    expect(text).toContain('Fix');
    expect(text).toContain('Commit');
    expect(text).toContain('Analysis');
  });

  it('should show chain names from parsed TOML', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\ndeveloper = ["claude-code"]\n';
    fixture.componentRef.setInput('toml', toml);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('developer');
    expect(el.textContent).toContain('claude-code');
  });

  it('should show drain binding from parsed TOML', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\ndeveloper = ["claude-code"]\n[agent_drains]\ndevelopment = "developer"\n';
    fixture.componentRef.setInput('toml', toml);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('developer');
  });

  it('should emit tomlChange when a drain binding changes', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\ndeveloper = ["claude-code"]\n[agent_drains]\ndevelopment = "developer"\n';
    fixture.componentRef.setInput('toml', toml);

    const changeSpy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(changeSpy);
    fixture.detectChanges();

    fixture.componentInstance.setDrainBinding('development', 'developer');
    expect(changeSpy).toHaveBeenCalled();
  });

  it('should show empty state when no chains configured', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.textContent).toContain('No chains configured');
  });

  // ── addChain ──

  it('addChain() adds a new chain with the given name', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    fixture.componentInstance.addChain('my-chain');
    expect(fixture.componentInstance.chainNames).toContain('my-chain');
  });

  it('addChain() emits tomlChange with the new chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    fixture.detectChanges();
    fixture.componentInstance.addChain('new-chain');
    expect(spy).toHaveBeenCalled();
    expect(spy.calls.mostRecent().args[0]).toContain('new-chain');
  });

  it('addChain() with duplicate name does not add a duplicate', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = []\n');
    fixture.detectChanges();
    fixture.componentInstance.addChain('developer');
    expect(fixture.componentInstance.chainNames.filter(n => n === 'developer').length).toBe(1);
  });

  it('addChain() with empty name does nothing', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    fixture.detectChanges();
    fixture.componentInstance.addChain('  ');
    expect(spy).not.toHaveBeenCalled();
  });

  // ── removeChain ──

  it('removeChain() removes the chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    fixture.componentInstance.removeChain('developer');
    expect(fixture.componentInstance.chainNames).not.toContain('developer');
  });

  it('removeChain() also removes drain bindings pointing to that chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\ndeveloper = []\n[agent_drains]\ndevelopment = "developer"\n';
    fixture.componentRef.setInput('toml', toml);
    fixture.detectChanges();
    fixture.componentInstance.removeChain('developer');
    expect(fixture.componentInstance.drainsMap['development']).toBeUndefined();
  });

  it('removeChain() emits tomlChange', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = []\n');
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    fixture.detectChanges();
    fixture.componentInstance.removeChain('developer');
    expect(spy).toHaveBeenCalled();
  });

  // ── addAgentToChain ──

  it('addAgentToChain() appends agent to specified chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = []\n');
    fixture.detectChanges();
    fixture.componentInstance.addAgentToChain('developer', 'claude-code');
    expect(fixture.componentInstance.getChainAgents('developer')).toContain('claude-code');
  });

  it('addAgentToChain() does not add duplicate agent', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    fixture.componentInstance.addAgentToChain('developer', 'claude-code');
    expect(fixture.componentInstance.getChainAgents('developer').filter(a => a === 'claude-code').length).toBe(1);
  });

  // ── removeAgentFromChain ──

  it('removeAgentFromChain() removes agent at index', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code", "codex"]\n');
    fixture.detectChanges();
    fixture.componentInstance.removeAgentFromChain('developer', 'claude-code');
    const agents = fixture.componentInstance.getChainAgents('developer');
    expect(agents).not.toContain('claude-code');
    expect(agents).toContain('codex');
  });

  // ── submitNewChain ──

  it('submitNewChain() adds chain and clears input', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    // Simulate typing
    fixture.componentInstance.onNewChainNameInput({ target: { value: 'test-chain' } } as unknown as Event);
    fixture.componentInstance.submitNewChain();
    expect(fixture.componentInstance.chainNames).toContain('test-chain');
    expect(fixture.componentInstance.newChainName).toBe('');
  });

  // ── submitNewAgent ──

  it('submitNewAgent() adds agent and clears input', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = []\n');
    fixture.detectChanges();
    fixture.componentInstance.onNewAgentInput('developer', { target: { value: 'codex' } } as unknown as Event);
    fixture.componentInstance.submitNewAgent('developer');
    expect(fixture.componentInstance.getChainAgents('developer')).toContain('codex');
    expect(fixture.componentInstance.getNewAgentInput('developer')).toBe('');
  });

  // ── Drag-drop: onAgentDrop ──

  it('onAgentDrop() reorders agents within a chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["agent-a", "agent-b", "agent-c"]\n');
    fixture.detectChanges();
    // Simulate moving agent from index 0 to index 2
    const dropEvent = {
      previousIndex: 0,
      currentIndex: 2,
      item: {},
      container: {},
      previousContainer: {},
      isPointerOverContainer: true,
      distance: { x: 0, y: 0 },
      dropPoint: { x: 0, y: 0 },
    } as unknown as CdkDragDrop<string[]>;
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    fixture.componentInstance.onAgentDrop('developer', dropEvent);
    const agents = fixture.componentInstance.getChainAgents('developer');
    expect(agents[2]).toBe('agent-a');
    expect(agents[0]).toBe('agent-b');
    expect(spy).toHaveBeenCalled();
  });

  it('onAgentDrop() does nothing when previousIndex equals currentIndex', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["agent-a", "agent-b"]\n');
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    const dropEvent = {
      previousIndex: 1,
      currentIndex: 1,
      item: {},
      container: {},
    } as unknown as CdkDragDrop<string[]>;
    fixture.componentInstance.onAgentDrop('developer', dropEvent);
    expect(spy).not.toHaveBeenCalled();
  });

  it('onAgentDrop() emits tomlChange with updated agent order', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["a", "b"]\n');
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    const dropEvent = {
      previousIndex: 1,
      currentIndex: 0,
      item: {},
      container: {},
    } as unknown as CdkDragDrop<string[]>;
    fixture.componentInstance.onAgentDrop('developer', dropEvent);
    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    // b should come before a now
    const bIdx = emittedToml.indexOf('"b"');
    const aIdx = emittedToml.indexOf('"a"');
    expect(bIdx).toBeLessThan(aIdx);
  });

  // ── Configured Agents section ──

  it('should render Configured Agents section', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('Configured Agents');
  });

  it('should show empty state in Configured Agents when no agents in chains', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('No agents configured');
  });

  it('should show agent cards for agents used in chains', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code", "codex"]\n');
    fixture.detectChanges();
    const cards = fixture.nativeElement.querySelectorAll('.agent-card');
    expect(cards.length).toBe(2);
  });

  it('should deduplicate agents across chains in Configured Agents', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\nchain1 = ["claude-code"]\nchain2 = ["claude-code", "codex"]\n';
    fixture.componentRef.setInput('toml', toml);
    fixture.detectChanges();
    const cards = fixture.nativeElement.querySelectorAll('.agent-card');
    // claude-code appears in both chains but should show only once
    expect(cards.length).toBe(2);
  });

  it('removeAgentFromAllChains() removes agent from all chains', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\nchain1 = ["claude-code", "codex"]\nchain2 = ["claude-code"]\n';
    fixture.componentRef.setInput('toml', toml);
    fixture.detectChanges();
    fixture.componentInstance.removeAgentFromAllChains('claude-code');
    expect(fixture.componentInstance.getChainAgents('chain1')).not.toContain('claude-code');
    expect(fixture.componentInstance.getChainAgents('chain2')).not.toContain('claude-code');
  });

  it('removeAgentFromAllChains() emits tomlChange', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    fixture.componentInstance.removeAgentFromAllChains('claude-code');
    expect(spy).toHaveBeenCalled();
  });

  // ── Chain with single agent drag affordance ──

  it('chain with single agent still renders drag handle', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["only-agent"]\n');
    fixture.detectChanges();
    const dragHandle = fixture.nativeElement.querySelector('.agent-chip__drag-handle');
    expect(dragHandle).not.toBeNull();
  });

  // ── Agent card tool/model metadata ──

  it('should show agent card with agent name', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    const card = fixture.nativeElement.querySelector('.agent-card');
    expect(card).not.toBeNull();
    expect(card.textContent).toContain('claude-code');
  });

  it('agent card renders tool metadata row', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    const card = fixture.nativeElement.querySelector('.agent-card');
    expect(card).not.toBeNull();
    // The card should have a tool/CLI metadata row
    const metaRows = card.querySelectorAll('.agent-card__meta-row');
    expect(metaRows.length).toBeGreaterThan(0);
  });

  it('configuredAgents exposes tool and model fields', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    const agents = fixture.componentInstance.configuredAgents();
    expect(agents.length).toBe(1);
    const agent = agents[0];
    expect(agent).toBeDefined();
    // tool and model should be strings (possibly empty if tool not found)
    expect(typeof agent!.tool).toBe('string');
    expect(typeof agent!.model).toBe('string');
  });

  // ── Remove agent confirmation ──

  it('requestRemoveAgent() removes agent immediately when not used in any chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    // Agent "standalone-agent" is not in any chain
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    // This agent isn't in chains so it should remove immediately (no confirmation needed)
    fixture.componentInstance.requestRemoveAgent('standalone-agent');
    // Should NOT trigger confirmation state (no pending remove)
    expect(fixture.componentInstance.pendingRemoveAgentName).toBeNull();
  });

  it('requestRemoveAgent() sets pendingRemoveAgentName when agent is used in a chain', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    fixture.componentInstance.requestRemoveAgent('claude-code');
    // Agent is in a chain, so confirmation should be pending
    expect(fixture.componentInstance.pendingRemoveAgentName).toBe('claude-code');
  });

  it('confirmRemoveAgent() removes agent from all chains after confirmation', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const toml = '[agent_chains]\nchain1 = ["claude-code", "codex"]\nchain2 = ["claude-code"]\n';
    fixture.componentRef.setInput('toml', toml);
    fixture.detectChanges();
    // Request removal (sets pending)
    fixture.componentInstance.requestRemoveAgent('claude-code');
    expect(fixture.componentInstance.pendingRemoveAgentName).toBe('claude-code');
    // Confirm removal
    fixture.componentInstance.confirmRemoveAgent();
    expect(fixture.componentInstance.getChainAgents('chain1')).not.toContain('claude-code');
    expect(fixture.componentInstance.getChainAgents('chain2')).not.toContain('claude-code');
    expect(fixture.componentInstance.pendingRemoveAgentName).toBeNull();
  });

  it('cancelRemoveAgent() clears the pending confirmation without removing', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);
    fixture.componentInstance.requestRemoveAgent('claude-code');
    expect(fixture.componentInstance.pendingRemoveAgentName).toBe('claude-code');
    fixture.componentInstance.cancelRemoveAgent();
    expect(fixture.componentInstance.pendingRemoveAgentName).toBeNull();
    // Agent should still be in the chain
    expect(fixture.componentInstance.getChainAgents('developer')).toContain('claude-code');
    // No tomlChange should have been emitted
    expect(spy).not.toHaveBeenCalled();
  });

  it('confirmation UI is rendered in template when pendingRemoveAgentName is set', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["claude-code"]\n');
    fixture.detectChanges();
    fixture.componentInstance.requestRemoveAgent('claude-code');
    fixture.detectChanges();
    const confirmEl = fixture.nativeElement.querySelector('[data-testid="remove-agent-confirm"]');
    expect(confirmEl).not.toBeNull();
  });

  // ── cdkDropList aria attributes ──

  it('cdkDropList has aria-label for keyboard navigation', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '[agent_chains]\ndeveloper = ["agent-a", "agent-b"]\n');
    fixture.detectChanges();
    const dropList = fixture.nativeElement.querySelector('[cdkdroplist]');
    expect(dropList).not.toBeNull();
  });

  // ── openAddAgentDialog() persists agent definition to TOML ──

  it('persistAgentDefinition() emits tomlChange with [agents.NAME] section', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    fixture.componentRef.setInput('toml', '');
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    fixture.componentInstance.persistAgentDefinition({ name: 'my-agent', tool: 'claude-code', model: 'claude-opus-4' });

    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).toContain('[agents.my-agent]');
    expect(emittedToml).toContain('tool = "claude-code"');
    expect(emittedToml).toContain('model = "claude-opus-4"');
  });

  it('persistAgentDefinition() preserves existing TOML content', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const initialToml = '[agent_chains]\ndeveloper = ["claude-code"]\n';
    fixture.componentRef.setInput('toml', initialToml);
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    fixture.componentInstance.persistAgentDefinition({ name: 'new-agent', tool: 'codex', model: '' });

    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).toContain('[agents.new-agent]');
    // Existing chains still present
    expect(emittedToml).toContain('[agent_chains]');
    expect(emittedToml).toContain('developer = ["claude-code"]');
  });

  it('persistAgentDefinition() replaces existing [agents.NAME] on re-save', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const initialToml = '[agents.my-agent]\ntool = "old-tool"\nmodel = "old-model"\n';
    fixture.componentRef.setInput('toml', initialToml);
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    fixture.componentInstance.persistAgentDefinition({ name: 'my-agent', tool: 'new-tool', model: 'new-model' });

    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).toContain('tool = "new-tool"');
    expect(emittedToml).not.toContain('old-tool');
    // Only one [agents.my-agent] section
    const count = (emittedToml.match(/\[agents\.my-agent\]/g) ?? []).length;
    expect(count).toBe(1);
  });

  // ── removeAgentDefinitionFromToml() — deletes [agents.NAME] section from TOML ──

  it('removeAgentDefinitionFromToml() emits tomlChange without the [agents.NAME] section', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const initialToml = '[agents.my-agent]\ntool = "claude-code"\nmodel = "model-x"\n[agent_chains]\ndeveloper = []\n';
    fixture.componentRef.setInput('toml', initialToml);
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    fixture.componentInstance.removeAgentDefinitionFromToml('my-agent');

    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).not.toContain('[agents.my-agent]');
    // Other content preserved
    expect(emittedToml).toContain('[agent_chains]');
  });

  it('removeAgentDefinitionFromToml() is a no-op when agent section does not exist', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const initialToml = '[defaults]\nverbosity = 2\n';
    fixture.componentRef.setInput('toml', initialToml);
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    fixture.componentInstance.removeAgentDefinitionFromToml('nonexistent');

    // Should still emit (to allow callers to update consistently)
    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).toContain('[defaults]');
  });

  // ── confirmRemoveAgent() cascades to [agents.NAME] section removal ──

  it('confirmRemoveAgent() removes agent from chains AND removes [agents.NAME] TOML section', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const initialToml = '[agents.claude-code]\ntool = "claude-code"\nmodel = "model-x"\n[agent_chains]\ndeveloper = ["claude-code"]\n';
    fixture.componentRef.setInput('toml', initialToml);
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    // Request and confirm removal
    fixture.componentInstance.requestRemoveAgent('claude-code');
    fixture.componentInstance.confirmRemoveAgent();

    // The last emitted TOML should have no [agents.claude-code] section
    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).not.toContain('[agents.claude-code]');
    expect(emittedToml).not.toContain('claude-code');
  });

  it('removeAgentFromAllChains() also removes [agents.NAME] TOML section', () => {
    const fixture = TestBed.createComponent(AgentChainsEditorComponent);
    const initialToml = '[agents.claude-code]\ntool = "claude-code"\n[agent_chains]\nchain1 = ["claude-code"]\n';
    fixture.componentRef.setInput('toml', initialToml);
    fixture.detectChanges();
    const spy = jasmine.createSpy('tomlChange');
    fixture.componentInstance.tomlChange.subscribe(spy);

    fixture.componentInstance.removeAgentFromAllChains('claude-code');

    expect(spy).toHaveBeenCalled();
    const emittedToml: string = spy.calls.mostRecent().args[0];
    expect(emittedToml).not.toContain('[agents.claude-code]');
  });
});
