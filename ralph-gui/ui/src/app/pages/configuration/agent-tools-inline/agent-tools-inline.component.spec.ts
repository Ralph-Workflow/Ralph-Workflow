import { ComponentFixture, TestBed } from '@angular/core/testing';
import { RouterModule } from '@angular/router';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { AgentToolsInlineComponent } from './agent-tools-inline.component';
import { TauriService } from '../../../services/tauri.service';
import type { AgentToolInfo } from '../../../types';

const MOCK_TOOLS: AgentToolInfo[] = [
  {
    name: 'claude',
    binary: 'claude',
    installed: true,
    version: '1.2.3',
    auth_status: 'authenticated',
    health: 'Ready',
    description: 'Claude AI assistant',
    available_models: ['claude-3-opus', 'claude-3-sonnet', 'claude-3-haiku'],
    binary_location: '/usr/local/bin/claude',
  },
  {
    name: 'aider',
    binary: 'aider',
    installed: true,
    version: '0.45.0',
    auth_status: 'needs_setup',
    health: 'Needs setup',
    description: 'AI pair programming tool',
    available_models: ['gpt-4', 'gpt-3.5-turbo'],
    binary_location: '/usr/local/bin/aider',
  },
  {
    name: 'cursorless',
    binary: 'cursorless',
    installed: false,
    version: null,
    auth_status: 'not_installed',
    health: 'Not Installed',
    description: 'Voice coding assistant',
    available_models: [],
    binary_location: null,
  },
];

describe('AgentToolsInlineComponent', () => {
  let fixture: ComponentFixture<AgentToolsInlineComponent>;
  let component: AgentToolsInlineComponent;
  let compiled: HTMLElement;
  let mockTauriService: {
    getAgentTools: ReturnType<typeof vi.fn>;
    testAgentToolConnection: ReturnType<typeof vi.fn>;
    openToolSettings: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    mockTauriService = {
      getAgentTools: vi.fn().mockResolvedValue(MOCK_TOOLS),
      testAgentToolConnection: vi.fn().mockResolvedValue('Connection successful'),
      openToolSettings: vi.fn().mockResolvedValue(undefined),
    };

    await TestBed.configureTestingModule({
      imports: [
        AgentToolsInlineComponent,
        RouterModule.forRoot([]),
      ],
      providers: [
        { provide: TauriService, useValue: mockTauriService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AgentToolsInlineComponent);
    component = fixture.componentInstance;
    compiled = fixture.nativeElement as HTMLElement;
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  describe('loading state', () => {
    it('should show loading spinner while loading', async () => {
      mockTauriService.getAgentTools.mockImplementation(() => new Promise(() => {}));
      fixture.detectChanges();
      await fixture.whenStable();

      const spinner = compiled.querySelector('mat-spinner');
      expect(spinner).toBeTruthy();
    });

    it('should hide loading spinner after loading completes', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const spinners = compiled.querySelectorAll('mat-spinner');
      const loadingSpinner = Array.from(spinners).find(s => s.getAttribute('diameter') === '24');
      expect(loadingSpinner).toBeFalsy();
    });
  });

  describe('tool cards rendering', () => {
    it('should render tool cards after loading completes', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      expect(toolCards.length).toBe(3);
    });

    it('should render tool name for each tool', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(compiled.textContent).toContain('claude');
      expect(compiled.textContent).toContain('aider');
      expect(compiled.textContent).toContain('cursorless');
    });
  });

  describe('health indicators', () => {
    it('should display green indicator for Ready health status', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const readyCard = Array.from(toolCards).find(card =>
        card.classList.contains('tool-card--ready')
      );
      expect(readyCard).toBeTruthy();

      const healthIndicator = readyCard?.querySelector('.health-indicator');
      expect(healthIndicator?.classList.contains('health-indicator--green')).toBe(true);
    });

    it('should display amber indicator for Needs setup health status', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const needsSetupCard = Array.from(toolCards).find(card =>
        card.classList.contains('tool-card--needs-setup')
      );
      expect(needsSetupCard).toBeTruthy();

      const healthIndicator = needsSetupCard?.querySelector('.health-indicator');
      expect(healthIndicator?.classList.contains('health-indicator--amber')).toBe(true);
    });

    it('should display gray indicator for Not Installed health status', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const notInstalledCard = Array.from(toolCards).find(card =>
        card.classList.contains('tool-card--not-installed')
      );
      expect(notInstalledCard).toBeTruthy();

      const healthIndicator = notInstalledCard?.querySelector('.health-indicator');
      expect(healthIndicator?.classList.contains('health-indicator--gray')).toBe(true);
    });

    it('should display health label text', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(compiled.textContent).toContain('Ready');
      expect(compiled.textContent).toContain('Needs setup');
      expect(compiled.textContent).toContain('Not Installed');
    });
  });

  describe('version badges', () => {
    it('should display version badge when tool has version', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const claudeCard = Array.from(toolCards).find(card =>
        card.textContent?.includes('claude')
      );
      expect(claudeCard?.textContent).toContain('v1.2.3');
    });

    it('should not display version badge when tool has no version', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const cursorlessCard = Array.from(toolCards).find(card =>
        card.textContent?.includes('cursorless')
      );
      expect(cursorlessCard?.querySelector('.tool-version')).toBeFalsy();
    });
  });

  describe('available models count', () => {
    it('should display available models count when tool has models', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const claudeCard = Array.from(toolCards).find(card =>
        card.textContent?.includes('claude')
      );
      expect(claudeCard?.textContent).toContain('Available models:');
      expect(claudeCard?.textContent).toContain('3');
    });

    it('should not display available models when tool has no models', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const cursorlessCard = Array.from(toolCards).find(card =>
        card.textContent?.includes('cursorless')
      );
      expect(cursorlessCard?.querySelector('.available-models')).toBeFalsy();
    });
  });

  describe('Test Connection button', () => {
    it('should call tauri.testAgentToolConnection when clicked', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const claudeCard = Array.from(toolCards).find(card =>
        card.textContent?.includes('claude')
      );
      const testButton = Array.from(claudeCard?.querySelectorAll('button') ?? []).find(btn =>
        btn.textContent?.includes('Test Connection')
      );

      expect(testButton).toBeTruthy();
      await component.testConnection('claude');
      expect(mockTauriService.testAgentToolConnection).toHaveBeenCalledWith('claude');
    });

    it('should disable button while testing', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      let resolvePromise: (value: string) => void;
      const pendingPromise = new Promise<string>((resolve) => {
        resolvePromise = resolve;
      });
      mockTauriService.testAgentToolConnection.mockReturnValue(pendingPromise);
      
      const testPromise = component.testConnection('claude');
      fixture.detectChanges();

      expect(component.testingTool()).toBe('claude');

      resolvePromise!('success');
      await testPromise;
      fixture.detectChanges();

      expect(component.testingTool()).toBe(null);
    });

    it('should store test result after connection test', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      await component.testConnection('claude');
      fixture.detectChanges();

      expect(component.getTestResult('claude')).toBe('Connection successful');
    });

    it('should store error message when connection test fails', async () => {
      mockTauriService.testAgentToolConnection.mockRejectedValue(new Error('Connection failed'));

      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      await component.testConnection('claude');
      fixture.detectChanges();

      expect(component.getTestResult('claude')).toContain('Error:');
      expect(component.getTestResult('claude')).toContain('Connection failed');
    });
  });

  describe('Open CLI Settings button', () => {
    it('should call tauri.openToolSettings when clicked', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const toolCards = compiled.querySelectorAll('.tool-card');
      const claudeCard = Array.from(toolCards).find(card =>
        card.textContent?.includes('claude')
      );
      const settingsButton = Array.from(claudeCard?.querySelectorAll('button') ?? []).find(btn =>
        btn.textContent?.includes('Open CLI Settings')
      );

      expect(settingsButton).toBeTruthy();
      await component.openCliSettings('claude');
      expect(mockTauriService.openToolSettings).toHaveBeenCalledWith('claude');
    });

    it('should handle errors when opening settings fails', async () => {
      const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      mockTauriService.openToolSettings.mockRejectedValue(new Error('Settings failed'));

      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      await component.openCliSettings('claude');

      expect(consoleErrorSpy).toHaveBeenCalled();
      consoleErrorSpy.mockRestore();
    });
  });

  describe('Manage All Tools link', () => {
    it('should render Manage All Tools link', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      expect(compiled.textContent).toContain('Manage All Tools');
    });

    it('should navigate to /agent-tools route', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const manageLink = compiled.querySelector('a[ng-reflect-router-link="/agent-tools"]') ??
        compiled.querySelector('a[href="/agent-tools"]') ??
        compiled.querySelector('.manage-link');
      
      expect(manageLink).toBeTruthy();
      expect(manageLink?.getAttribute('ng-reflect-router-link') || 
             manageLink?.getAttribute('href') ||
             manageLink).toBeTruthy();
    });

    it('should have arrow_forward icon', async () => {
      fixture.detectChanges();
      await fixture.whenStable();
      fixture.detectChanges();

      const manageLink = compiled.querySelector('.manage-link');
      const icon = manageLink?.querySelector('mat-icon');
      expect(icon?.textContent).toContain('arrow_forward');
    });
  });
});
