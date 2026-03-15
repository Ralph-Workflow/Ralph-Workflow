import { TestBed } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { AgentToolsComponent } from './agent-tools.component';
import { TauriService } from '../../services/tauri.service';
import type { AgentToolInfo, ToolUpdateInfo } from '../../types';

const MOCK_TOOLS: AgentToolInfo[] = [
  {
    name: 'Claude Code',
    binary: 'claude',
    installed: true,
    version: '1.2.3',
    auth_status: 'authenticated',
    health: 'ready',
    description: 'Developer agent CLI',
    available_models: ['opus', 'sonnet'],
    binary_location: '/usr/local/bin/claude',
  },
  {
    name: 'Codex',
    binary: 'codex',
    installed: false,
    version: null,
    auth_status: 'unknown',
    health: 'not_installed',
    description: 'OpenAI coding CLI',
    available_models: [],
    binary_location: null,
  },
  {
    name: 'OpenCode',
    binary: 'opencode',
    installed: true,
    version: '0.1.0',
    auth_status: 'unauthenticated',
    health: 'needs_setup',
    description: 'Multi-provider CLI',
    available_models: ['gpt-4'],
    binary_location: '/usr/local/bin/opencode',
  },
];

const MOCK_UPDATE_INFO: ToolUpdateInfo[] = [
  {
    name: 'Claude Code',
    current_version: '1.2.0',
    latest_version: '1.3.0',
    update_available: true,
    message: 'improved auth detection, better model metadata',
  },
  {
    name: 'OpenCode',
    current_version: '0.1.0',
    latest_version: '0.2.0',
    update_available: false,
    message: '',
  },
];

function createMockTauriService() {
  return {
    getAgentTools: vi.fn().mockResolvedValue(MOCK_TOOLS),
    testAgentToolConnection: vi.fn().mockResolvedValue('Connected: v1.2.3'),
    checkToolUpdates: vi.fn().mockResolvedValue(MOCK_UPDATE_INFO),
    installAgentTool: vi.fn().mockResolvedValue(undefined),
    refreshToolModels: vi.fn().mockResolvedValue(['opus', 'sonnet', 'haiku']),
    openToolSettings: vi.fn().mockResolvedValue(undefined),
  };
}

describe('AgentToolsComponent', () => {
  let mockTauriService: ReturnType<typeof createMockTauriService>;

  async function createComponent() {
    mockTauriService = createMockTauriService();

    await TestBed.configureTestingModule({
      imports: [AgentToolsComponent],
      providers: [
        provideZonelessChangeDetection(),
        { provide: TauriService, useValue: mockTauriService },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(AgentToolsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return { fixture, component: fixture.componentInstance };
  }

  beforeEach(() => {
    TestBed.resetTestingModule();
  });

  it('should create the component', async () => {
    const { component } = await createComponent();
    expect(component).toBeTruthy();
  });

  it('should call getAgentTools on init', async () => {
    await createComponent();
    expect(mockTauriService.getAgentTools).toHaveBeenCalled();
  });

  it('should populate tools after loading', async () => {
    const { component } = await createComponent();
    expect(component.tools.length).toBe(3);
  });

  it('should set isLoading false after load', async () => {
    const { component } = await createComponent();
    expect(component.isLoading).toBe(false);
  });

  describe('AC-14.1: Tool Cards rendering', () => {
    it('should render a card for each tool', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Claude Code');
      expect(compiled.textContent).toContain('Codex');
      expect(compiled.textContent).toContain('OpenCode');
    });

    it('should show health status label for each tool', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Ready to use');
      expect(compiled.textContent).toContain('Not installed');
      expect(compiled.textContent).toContain('Needs setup');
    });

    it('should show installed version when available', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('v1.2.3');
      expect(compiled.textContent).toContain('v0.1.0');
    });
    it('should show description for each tool', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Developer agent CLI');
      expect(compiled.textContent).toContain('OpenAI coding CLI');
      expect(compiled.textContent).toContain('Multi-provider CLI');
    });
    it('should show binary location when available', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('/usr/local/bin/claude');
      expect(compiled.textContent).toContain('/usr/local/bin/opencode');
    });
    it('should show available models for installed tools', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('opus, sonnet');
    });
    it('should show auth status for installed tools', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('authenticated');
      expect(compiled.textContent).toContain('unauthenticated');
    });
    it('should show "No models detected" for installed tools with empty models', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Models:');
    });
  });

  describe('AC-14.2: Tool Actions', () => {
    describe('Test Connection', () => {
      it('should call testAgentToolConnection with tool name', async () => {
        const { component } = await createComponent();
        await component.testConnection('Claude Code');
        expect(mockTauriService.testAgentToolConnection).toHaveBeenCalledWith('Claude Code');
      });

      it('should store connection result in testResults map', async () => {
        const { fixture, component } = await createComponent();
        await component.testConnection('Claude Code');
        fixture.detectChanges();
        expect(component.testResults['Claude Code']?.message).toBe('Connected: v1.2.3');
        expect(component.testResults['Claude Code']?.ok).toBe(true);
      });

      it('should store error message when connection fails', async () => {
        const { fixture, component } = await createComponent();
        mockTauriService.testAgentToolConnection.mockRejectedValueOnce(new Error('Not found'));
        await component.testConnection('Claude Code');
        fixture.detectChanges();
        const result = component.testResults['Claude Code'];
        expect(result).toBeTruthy();
        expect(result?.message).toContain('Not found');
        expect(result?.ok).toBe(false);
      });
    });

    describe('Open CLI Settings', () => {
      it('should call openToolSettings with tool name', async () => {
        const { component } = await createComponent();
        await component.openToolSettings('Claude Code');
        expect(mockTauriService.openToolSettings).toHaveBeenCalledWith('Claude Code');
      });
    });

    describe('Check for Updates', () => {
      it('should call checkToolUpdates', async () => {
        const { component } = await createComponent();
        await component.checkForUpdates();
        expect(mockTauriService.checkToolUpdates).toHaveBeenCalled();
      });

      it('should store update info', async () => {
        const { fixture, component } = await createComponent();
        await component.checkForUpdates();
        fixture.detectChanges();
        expect(component.updateInfo.length).toBe(2);
      });

      it('should open update dialog when update is available', async () => {
        const { fixture, component } = await createComponent();
        await component.checkForUpdates();
        fixture.detectChanges();
        expect(component.updateDialogTool).toBeTruthy();
        expect(component.updateDialogTool?.name).toBe('Claude Code');
      });

      it('should set checkingUpdates flag during check', async () => {
        const { fixture, component } = await createComponent();
        let checkResolved = false;
        component.checkForUpdates().then(() => {
          checkResolved = true;
        });
        fixture.detectChanges();
        expect(component.checkingUpdates).toBe(true);
        await new Promise((resolve) => setTimeout(resolve, 10));
        fixture.detectChanges();
        expect(checkResolved).toBe(true);
        expect(component.checkingUpdates).toBe(false);
      });
    });

    describe('Refresh Models', () => {
      it('should call refreshToolModels with tool name', async () => {
        const { component } = await createComponent();
        await component.refreshModels('Claude Code');
        expect(mockTauriService.refreshToolModels).toHaveBeenCalledWith('Claude Code');
      });

      it('should set refreshing flag during refresh', async () => {
        const { fixture, component } = await createComponent();
        let refreshResolved = false;
        component.refreshModels('Claude Code').then(() => {
          refreshResolved = true;
        });
        fixture.detectChanges();
        expect(component.isToolRefreshingModels('Claude Code')).toBe(true);
        await new Promise((resolve) => setTimeout(resolve, 10));
        fixture.detectChanges();
        expect(refreshResolved).toBe(true);
        expect(component.isToolRefreshingModels('Claude Code')).toBe(false);
      });
    });

    describe('Install Tool', () => {
      it('should open install dialog with correct tool name', async () => {
        const { fixture, component } = await createComponent();
        component.openInstallDialog('Codex');
        fixture.detectChanges();
        expect(component.installDialogTool).toBe('Codex');
      });

      it('should close install dialog', async () => {
        const { fixture, component } = await createComponent();
        component.openInstallDialog('Codex');
        component.closeInstallDialog();
        fixture.detectChanges();
        expect(component.installDialogTool).toBeNull();
      });

      it('should call installAgentTool when installing', async () => {
        const { component } = await createComponent();
        await component.installTool('Codex');
        expect(mockTauriService.installAgentTool).toHaveBeenCalledWith('Codex');
      });

      it('should set installing flag during install', async () => {
        const { fixture, component } = await createComponent();
        component.openInstallDialog('Codex');
        let installResolved = false;
        component.installTool('Codex').then(() => {
          installResolved = true;
        });
        fixture.detectChanges();
        expect(component.isToolInstalling('Codex')).toBe(true);
        await new Promise((resolve) => setTimeout(resolve, 10));
        fixture.detectChanges();
        expect(installResolved).toBe(true);
        expect(component.isToolInstalling('Codex')).toBe(false);
      });
    });
  });

  describe('AC-14.3: Tool States', () => {
    it('should show Install button for not-installed tools', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Install');
    });

    it('should show Test Connection for installed tools', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Test Connection');
    });

    it('should show Open CLI Settings for installed tools', async () => {
      const { fixture } = await createComponent();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Open CLI Settings');
    });

    it('should show Update available badge when update is available', async () => {
      const { fixture, component } = await createComponent();
      await component.checkForUpdates();
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Update available');
    });
  });

  describe('Install Dialog', () => {
    it('should set default install method to homebrew', async () => {
      const { fixture, component } = await createComponent();
      component.openInstallDialog('Codex');
      fixture.detectChanges();
      expect(component.installMethod).toBe('homebrew');
    });

    it('should change install method', async () => {
      const { fixture, component } = await createComponent();
      component.openInstallDialog('Codex');
      component.setInstallMethod('bun');
      fixture.detectChanges();
      expect(component.installMethod).toBe('bun');
    });

    it('should return correct install command for tool and method', async () => {
      const { component } = await createComponent();
      const cmd = component.getInstallCommand('Claude Code', 'homebrew');
      expect(cmd).toContain('brew install');
    });

    it('should return fallback for unknown tool', async () => {
      const { component } = await createComponent();
      const cmd = component.getInstallCommand('Unknown Tool', 'homebrew');
      expect(cmd).toContain('brew install');
    });
  });

  describe('Update Dialog', () => {
    it('should open update dialog with ToolUpdateInfo', async () => {
      const { fixture, component } = await createComponent();
      await component.checkForUpdates();
      fixture.detectChanges();
      expect(component.updateDialogTool).toBeTruthy();
      expect(component.updateDialogTool?.current_version).toBe('1.2.0');
      expect(component.updateDialogTool?.latest_version).toBe('1.3.0');
    });

    it('should close update dialog', async () => {
      const { fixture, component } = await createComponent();
      const info: ToolUpdateInfo = {
        name: 'Test',
        current_version: '1.0.0',
        latest_version: '2.0.0',
        update_available: true,
        message: 'test',
      };
      component.openUpdateDialog(info);
      component.closeUpdateDialog();
      fixture.detectChanges();
      expect(component.updateDialogTool).toBeNull();
    });

    it('should close update dialog after installing', async () => {
      const { fixture, component } = await createComponent();
      await component.checkForUpdates();
      fixture.detectChanges();
      const info = component.updateDialogTool;
      expect(info).toBeTruthy();
      await component.installTool(info!.name);
      fixture.detectChanges();
      expect(component.updateDialogTool).toBeNull();
    });
  });

  describe('Update Info Lookup', () => {
    it('should return update info for a tool name', async () => {
      const { fixture, component } = await createComponent();
      await component.checkForUpdates();
      fixture.detectChanges();
      const info = component.getUpdateInfo('Claude Code');
      expect(info).toBeTruthy();
      expect(info?.update_available).toBe(true);
    });

    it('should return undefined for tool without update info', async () => {
      const { fixture, component } = await createComponent();
      await component.checkForUpdates();
      fixture.detectChanges();
      const info = component.getUpdateInfo('Nonexistent Tool');
      expect(info).toBeUndefined();
    });
  });

  describe('refresh', () => {
    it('should reload tools on refresh', async () => {
      const { component } = await createComponent();
      mockTauriService.getAgentTools.mockClear();
      await component.refresh();
      expect(mockTauriService.getAgentTools).toHaveBeenCalled();
    });
  });

  describe('health display', () => {
    it('should return "Ready to use" label for ready health', async () => {
      const { component } = await createComponent();
      expect(component.healthLabels['ready']).toBe('Ready to use');
    });

    it('should return "Needs setup" label for needs_setup health', async () => {
      const { component } = await createComponent();
      expect(component.healthLabels['needs_setup']).toBe('Needs setup');
    });

    it('should return "Not installed" label for not_installed health', async () => {
      const { component } = await createComponent();
      expect(component.healthLabels['not_installed']).toBe('Not installed');
    });
  });
});

describe('AgentToolsComponent - error handling', () => {
  async function createComponent() {
    const errorMockService = {
      ...createMockTauriService(),
      getAgentTools: vi.fn().mockRejectedValue(new Error('Backend unavailable')),
    };

    await TestBed.configureTestingModule({
      imports: [AgentToolsComponent],
      providers: [
        provideZonelessChangeDetection(),
        { provide: TauriService, useValue: errorMockService },
      ],
    }).compileComponents();

    const fixture = TestBed.createComponent(AgentToolsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    return { fixture, component: fixture.componentInstance };
  }

  it('should handle load error gracefully', async () => {
    const { fixture, component } = await createComponent();
    fixture.detectChanges();
    expect(component.tools).toEqual([]);
    expect(component.isLoading).toBe(false);
  });
});
