import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { AgentToolsComponent } from './agent-tools.component';
import { TAURI_INVOKE } from '../../services/tauri.service';
import type { AgentToolInfo } from '../../types';

const MOCK_TOOLS: AgentToolInfo[] = [
  {
    name: 'Claude Code',
    binary: 'claude',
    installed: true,
    version: '1.2.3',
    auth_status: 'authenticated',
    health: 'ready',
    description: 'Anthropic Claude Code CLI',
    available_models: ['claude-sonnet-4-6', 'claude-opus-4-5'],
    binary_location: '/usr/local/bin/claude',
  },
  {
    name: 'Codex',
    binary: 'codex',
    installed: false,
    version: null,
    auth_status: 'unknown',
    health: 'not_installed',
    description: 'OpenAI Codex CLI',
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
    description: 'OpenCode AI CLI',
    available_models: ['gpt-4'],
    binary_location: '/usr/local/bin/opencode',
  },
];

describe('AgentToolsComponent', () => {
  let component: AgentToolsComponent;
  let fixture: ComponentFixture<AgentToolsComponent>;
  let mockInvoke: jasmine.Spy;

  beforeEach(async () => {
    mockInvoke = jasmine.createSpy('invoke').and.callFake((cmd: string) => {
      switch (cmd) {
        case 'get_agent_tools':
          return Promise.resolve(MOCK_TOOLS);
        case 'test_agent_tool_connection':
          return Promise.resolve('Connected: v1.2.3');
        default:
          return Promise.reject(new Error(`Unknown command: ${cmd}`));
      }
    });

    await TestBed.configureTestingModule({
      imports: [AgentToolsComponent],
      providers: [
        { provide: TAURI_INVOKE, useValue: mockInvoke },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(AgentToolsComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create the component', () => {
    expect(component).toBeTruthy();
  });

  describe('loading tools', () => {
    it('should call get_agent_tools on init', fakeAsync(() => {
      tick();
      expect(mockInvoke).toHaveBeenCalledWith('get_agent_tools');
    }));

    it('should populate tools signal after loading', fakeAsync(() => {
      tick();
      expect(component.tools.length).toBe(3);
    }));

    it('should set isLoading false after load', fakeAsync(() => {
      tick();
      expect(component.isLoading).toBe(false);
    }));

    it('should handle load error gracefully', fakeAsync(() => {
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'get_agent_tools') return Promise.reject(new Error('Backend unavailable'));
        return Promise.resolve(undefined);
      });
      fixture = TestBed.createComponent(AgentToolsComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick();
      expect(component.tools).toEqual([]);
      expect(component.isLoading).toBe(false);
    }));
  });

  describe('tool cards rendering', () => {
    it('should render a card for each tool', fakeAsync(() => {
      tick();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Claude Code');
      expect(compiled.textContent).toContain('Codex');
      expect(compiled.textContent).toContain('OpenCode');
    }));

    it('should show health status for each tool', fakeAsync(() => {
      tick();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('Ready');
      expect(compiled.textContent).toContain('Not installed');
    }));

    it('should show installed version when available', fakeAsync(() => {
      tick();
      fixture.detectChanges();
      const compiled = fixture.nativeElement as HTMLElement;
      expect(compiled.textContent).toContain('1.2.3');
    }));
  });

  describe('test connection', () => {
    it('should call test_agent_tool_connection with tool name', fakeAsync(async () => {
      tick();
      fixture.detectChanges();

      await component.testConnection('Claude Code');
      tick();

      expect(mockInvoke).toHaveBeenCalledWith('test_agent_tool_connection', { name: 'Claude Code' });
    }));

    it('should store connection result in testResults map', fakeAsync(async () => {
      tick();
      fixture.detectChanges();

      await component.testConnection('Claude Code');
      tick();

      expect(component.testResults['Claude Code']?.message).toBe('Connected: v1.2.3');
      expect(component.testResults['Claude Code']?.ok).toBe(true);
    }));

    it('should store error message in testResults when connection fails', fakeAsync(async () => {
      mockInvoke.and.callFake((cmd: string) => {
        if (cmd === 'get_agent_tools') return Promise.resolve(MOCK_TOOLS);
        if (cmd === 'test_agent_tool_connection') return Promise.reject(new Error('Not found'));
        return Promise.reject(new Error(`Unknown: ${cmd}`));
      });
      fixture = TestBed.createComponent(AgentToolsComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      tick();

      await component.testConnection('Codex');
      tick();

      const result = component.testResults['Codex'];
      expect(result).toBeTruthy();
      expect(result?.message).toContain('Not found');
    }));
  });

  describe('refresh', () => {
    it('should reload tools on refresh', fakeAsync(async () => {
      tick();
      const callCount = mockInvoke.calls.count();

      await component.refresh();
      tick();

      expect(mockInvoke.calls.count()).toBeGreaterThan(callCount);
    }));
  });

  describe('health display', () => {
    it('should return "Ready" label for ready health', () => {
      expect(component.healthLabels['ready']).toBe('Ready');
    });

    it('should return "Needs setup" label for needs_setup health', () => {
      expect(component.healthLabels['needs_setup']).toBe('Needs setup');
    });

    it('should return "Not installed" label for not_installed health', () => {
      expect(component.healthLabels['not_installed']).toBe('Not installed');
    });
  });
});
