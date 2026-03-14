import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { RouterModule } from '@angular/router';
import { OnboardingComponent } from './onboarding.component';
import { describe, it, expect, beforeEach, vi } from 'vitest';

import { WorkspaceService } from '../../services/workspace.service';
import { TAURI_INVOKE } from '../../services/tauri.service';
import type { AgentToolInfo } from '../../types';

describe('OnboardingComponent', () => {
  let component: OnboardingComponent;
  let fixture: ComponentFixture<OnboardingComponent>;
  let router: Router;

  const mockTools: AgentToolInfo[] = [
    { name: 'Claude Code', binary: 'claude', installed: true, version: '1.0.0', auth_status: 'authenticated', health: 'healthy', description: 'Anthropic Claude Code CLI', available_models: ['claude-sonnet-4-6'], binary_location: '/usr/local/bin/claude' },
    { name: 'Codex', binary: 'codex', installed: false, version: null, auth_status: 'unknown', health: 'not-installed', description: 'OpenAI Codex CLI', available_models: [], binary_location: null },
    { name: 'OpenCode', binary: 'opencode', installed: false, version: null, auth_status: 'unknown', health: 'not-installed', description: 'OpenCode AI CLI', available_models: [], binary_location: null },
  ];

  const mockInvoke = vi.fn().mockImplementation((cmd: string) => {
    if (cmd === 'get_agent_tools') return Promise.resolve(mockTools);
    if (cmd === 'open_workspace') return Promise.resolve({ id: '1', repo_path: '/test', display_name: 'test', last_nav: '', active_run_count: 0 });
    return Promise.resolve(null);
  });

  const mockWorkspaceService = {
    openWorkspace: vi.fn().mockReturnValue(
      Promise.resolve({ id: '1', path: '/test', label: 'test', activeWorktree: null, runSummary: { running: 0, failed: 0, paused: 0 }, navigationState: null, activeRunCount: 0 }),
    ),
  };

  beforeEach(async () => {
    mockInvoke.mockClear();
    (mockWorkspaceService.openWorkspace as ReturnType<typeof vi.fn>).mockClear();

    await TestBed.configureTestingModule({
      imports: [OnboardingComponent, RouterModule.forRoot([])],
      providers: [
        { provide: TAURI_INVOKE, useValue: mockInvoke },
        { provide: WorkspaceService, useValue: mockWorkspaceService },
      ],
    }).compileComponents();

    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(OnboardingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  describe('step 1 - Welcome', () => {
    it('should start on step 1', () => {
      expect(component.currentStep()).toBe(1);
    });

    it('should render step 1 content with Ralph branding', () => {
      fixture.detectChanges();
      const heading = fixture.nativeElement.querySelector('h1, h2, .welcome-title');
      expect(heading).not.toBeNull();
      expect(heading.textContent).toContain('Ralph');
    });

    it('should have "Get Started" and "Skip" buttons on step 1', () => {
      fixture.detectChanges();
      const buttons = fixture.nativeElement.querySelectorAll('button');
      const buttonTexts = Array.from(buttons as NodeListOf<HTMLButtonElement>).map((b) => b.textContent?.trim());
      expect(buttonTexts).toContain('Get Started');
      expect(buttonTexts.some(t => t?.toLowerCase().includes('skip'))).toBe(true);
    });

    it('should advance to step 2 when "Get Started" is clicked', async () => {
      fixture.detectChanges();
      const getStartedBtn = Array.from(
        fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
      ).find(b => b.textContent?.trim() === 'Get Started');
      expect(getStartedBtn).not.toBeUndefined();
      getStartedBtn!.click();
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      expect(component.currentStep()).toBe(2);
    });

    it('should navigate to "/" when Skip is clicked on step 1', async () => {
      const navigateSpy = spyOn(router, 'navigate');
      fixture.detectChanges();
      const skipBtn = Array.from(
        fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
      ).find(b => b.textContent?.toLowerCase().includes('skip'));
      expect(skipBtn).not.toBeUndefined();
      skipBtn!.click();
      await fixture.whenStable();
      expect(navigateSpy).toHaveBeenCalledWith(['/']);
    });

    it('should show progress indicator for step 1/3', () => {
      fixture.detectChanges();
      const progress = fixture.nativeElement.querySelector('.progress-indicator, [aria-label*="step"], .step-dots');
      expect(progress).not.toBeNull();
    });
  });

  describe('step 2 - Agent Tools', () => {
    beforeEach(async () => {
      component.goToStep(2);
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
    });

    it('should show step 2 content', () => {
      expect(component.currentStep()).toBe(2);
    });

    it('should call getAgentTools on entering step 2', async () => {
      component.goToStep(2);
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const calledCommands = mockInvoke.mock.calls.map(args => args[0]);
      expect(calledCommands).toContain('get_agent_tools');
    });

    it('should display agent tools after loading', async () => {
      component.goToStep(2);
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const toolItems = fixture.nativeElement.querySelectorAll('.tool-item, .agent-tool');
      expect(toolItems.length).toBeGreaterThan(0);
    });

    it('should show tool health/installed status', async () => {
      component.goToStep(2);
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const nativeEl = fixture.nativeElement as HTMLElement;
      // Check that "healthy" or "installed" text appears
      expect(nativeEl.textContent).toMatch(/healthy|installed/i);
    });

    it('should navigate to "/" when Skip is clicked on step 2', async () => {
      const navigateSpy = spyOn(router, 'navigate');
      component.goToStep(2);
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const skipBtn = Array.from(
        fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
      ).find(b => b.textContent?.toLowerCase().includes('skip'));
      expect(skipBtn).not.toBeUndefined();
      skipBtn!.click();
      await fixture.whenStable();
      expect(navigateSpy).toHaveBeenCalledWith(['/']);
    });
  });

  describe('step 3 - Open Workspace', () => {
    beforeEach(() => {
      component.goToStep(3);
      fixture.detectChanges();
    });

    it('should show step 3 content', () => {
      expect(component.currentStep()).toBe(3);
    });

    it('should have an "Open" directory button', () => {
      const buttons = fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>;
      const openBtn = Array.from(buttons).find(b =>
        b.textContent?.toLowerCase().includes('open') ||
        b.textContent?.toLowerCase().includes('browse'),
      );
      expect(openBtn).not.toBeUndefined();
    });

    it('should navigate to "/" when Skip is clicked on step 3', async () => {
      const navigateSpy = spyOn(router, 'navigate');
      fixture.detectChanges();
      const skipBtn = Array.from(
        fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
      ).find(b => b.textContent?.toLowerCase().includes('skip'));
      expect(skipBtn).not.toBeUndefined();
      skipBtn!.click();
      await fixture.whenStable();
      expect(navigateSpy).toHaveBeenCalledWith(['/']);
    });
  });

  describe('navigation', () => {
    it('should go back from step 2 to step 1 with Back button', async () => {
      component.goToStep(2);
      await new Promise(r => setTimeout(r, 100));
      fixture.detectChanges();
      const backBtn = Array.from(
        fixture.nativeElement.querySelectorAll('button') as NodeListOf<HTMLButtonElement>,
      ).find(b => b.textContent?.toLowerCase().includes('back'));
      expect(backBtn).not.toBeUndefined();
      backBtn!.click();
      fixture.detectChanges();
      expect(component.currentStep()).toBe(1);
    });
  });
});
