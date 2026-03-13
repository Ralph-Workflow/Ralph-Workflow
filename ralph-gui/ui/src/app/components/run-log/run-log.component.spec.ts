import { TestBed, fakeAsync, tick } from '@angular/core/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { RunLogComponent, parseAnsiToHtml, VIRTUAL_SCROLL_ITEM_SIZE } from './run-log.component';
import { TAURI_INVOKE } from '../../services/tauri.service';

// We must mock the @tauri-apps/api/event listen function at module level.
// Jest or Jasmine spy won't capture ES module exports directly, so we inject
// a LISTEN token instead (see component implementation).
import { LISTEN_TOKEN } from './run-log.component';

describe('parseAnsiToHtml (pure function)', () => {
  it('should convert bold ANSI escape to bold span', () => {
    const result = parseAnsiToHtml('\x1b[1mhello\x1b[0m');
    expect(result).toContain('font-weight:bold');
    expect(result).toContain('hello');
  });

  it('should convert red foreground (31) to color span', () => {
    const result = parseAnsiToHtml('\x1b[31mred text\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('red text');
  });

  it('should convert green foreground (32) to color span', () => {
    const result = parseAnsiToHtml('\x1b[32mgreen\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('green');
  });

  it('should convert yellow foreground (33) to color span', () => {
    const result = parseAnsiToHtml('\x1b[33myellow\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('yellow');
  });

  it('should convert blue foreground (34) to color span', () => {
    const result = parseAnsiToHtml('\x1b[34mblue\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('blue');
  });

  it('should convert magenta foreground (35) to color span', () => {
    const result = parseAnsiToHtml('\x1b[35mmagenta\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('magenta');
  });

  it('should convert cyan foreground (36) to color span', () => {
    const result = parseAnsiToHtml('\x1b[36mcyan\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('cyan');
  });

  it('should convert white foreground (37) to color span', () => {
    const result = parseAnsiToHtml('\x1b[37mwhite\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('white');
  });

  it('should handle bright colors (90-97)', () => {
    const result = parseAnsiToHtml('\x1b[90mbright-black\x1b[0m');
    expect(result).toContain('color:');
    expect(result).toContain('bright-black');
  });

  it('should handle reset with \\x1b[m (no code)', () => {
    const result = parseAnsiToHtml('\x1b[1mbold\x1b[mend');
    expect(result).toContain('bold');
    expect(result).toContain('end');
  });

  it('should strip unknown escape sequences safely', () => {
    const result = parseAnsiToHtml('\x1b[999mtext\x1b[0m');
    expect(result).toContain('text');
    // Unknown escape should NOT appear literally in output
    expect(result).not.toContain('\x1b');
  });

  it('should handle plain text with no ANSI codes', () => {
    const result = parseAnsiToHtml('plain text');
    expect(result).toBe('plain text');
  });

  it('should handle empty string', () => {
    const result = parseAnsiToHtml('');
    expect(result).toBe('');
  });

  it('should close open spans after reset', () => {
    const result = parseAnsiToHtml('\x1b[31mred\x1b[0m normal');
    // After reset, subsequent text should not have color span
    expect(result).toContain('normal');
    // The open span should be closed
    const openCount = (result.match(/<span/g) ?? []).length;
    const closeCount = (result.match(/<\/span>/g) ?? []).length;
    expect(openCount).toBe(closeCount);
  });
});

describe('RunLogComponent', () => {
  let tauriInvokeSpy: jasmine.Spy;
  let listenSpy: jasmine.Spy;
  let unlistenSpy: jasmine.Spy;

  beforeEach(async () => {
    tauriInvokeSpy = jasmine.createSpy('invoke').and.returnValue(Promise.resolve());
    unlistenSpy = jasmine.createSpy('unlisten');
    listenSpy = jasmine.createSpy('listen').and.returnValue(Promise.resolve(unlistenSpy));

    await TestBed.configureTestingModule({
      imports: [RunLogComponent],
      providers: [
        provideZonelessChangeDetection(),
        { provide: TAURI_INVOKE, useValue: tauriInvokeSpy },
        { provide: LISTEN_TOKEN, useValue: listenSpy },
      ],
    }).compileComponents();
  });

  function createComponent(runId = 'run-123', repoPath = '/repo', worktreePath: string | null = null) {
    const fixture = TestBed.createComponent(RunLogComponent);
    const component = fixture.componentInstance;
    // Set required input via TestBed's setInput
    fixture.componentRef.setInput('runId', runId);
    fixture.componentRef.setInput('repoPath', repoPath);
    fixture.componentRef.setInput('worktreePath', worktreePath);
    return { fixture, component };
  }

  it('should create', () => {
    const { fixture, component } = createComponent();
    fixture.detectChanges();
    expect(component).toBeTruthy();
  });

  it('should call subscribeRunLogs on init', fakeAsync(async () => {
    const { fixture } = createComponent('run-abc', '/repo/path', '/wt/path');
    fixture.detectChanges();
    tick();
    expect(tauriInvokeSpy).toHaveBeenCalledWith('subscribe_run_logs', jasmine.objectContaining({
      run_id: 'run-abc',
    }));
  }));

  it('should call listen with run-log-{runId} event name on init', fakeAsync(async () => {
    const { fixture } = createComponent('run-abc', '/repo', null);
    fixture.detectChanges();
    tick();
    expect(listenSpy).toHaveBeenCalledWith('run-log-run-abc', jasmine.any(Function));
  }));

  it('should call unsubscribeRunLogs and unlisten on destroy', fakeAsync(async () => {
    const { fixture } = createComponent('run-xyz');
    fixture.detectChanges();
    tick();
    fixture.destroy();
    expect(tauriInvokeSpy).toHaveBeenCalledWith('unsubscribe_run_logs', jasmine.objectContaining({
      run_id: 'run-xyz',
    }));
    expect(unlistenSpy).toHaveBeenCalled();
  }));

  describe('autoScroll', () => {
    it('should default autoScroll to true', () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      expect(component.autoScroll()).toBe(true);
    });

    it('should toggle autoScroll off', () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      component.setAutoScroll(false);
      expect(component.autoScroll()).toBe(false);
    });

    it('should show Resume auto-scroll button when autoScroll is false', () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      component.setAutoScroll(false);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="resume-autoscroll"]')).toBeTruthy();
    });

    it('should NOT show Resume auto-scroll button when autoScroll is true', () => {
      const { fixture } = createComponent();
      fixture.detectChanges();
      // autoScroll is true by default
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="resume-autoscroll"]')).toBeNull();
    });

    it('should re-enable autoScroll when resume button is clicked', () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      component.setAutoScroll(false);
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      const btn = el.querySelector<HTMLElement>('[data-testid="resume-autoscroll"]');
      btn?.click();
      expect(component.autoScroll()).toBe(true);
    });
  });

  describe('search filter', () => {
    it('should show all lines when search term is empty', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      // Simulate receiving log lines
      component.addLogLine('line one');
      component.addLogLine('line two');
      component.addLogLine('line three');
      fixture.detectChanges();

      expect(component.filteredLines().length).toBe(3);
    }));

    it('should filter lines matching search term', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      component.addLogLine('hello world');
      component.addLogLine('goodbye world');
      component.addLogLine('something else');
      component.searchTerm.set('world');
      fixture.detectChanges();

      expect(component.filteredLines().length).toBe(2);
    }));

    it('should be case-insensitive in search', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      component.addLogLine('Hello World');
      component.searchTerm.set('hello');
      fixture.detectChanges();

      expect(component.filteredLines().length).toBe(1);
    }));

    it('should return empty when no lines match search term', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      component.addLogLine('hello world');
      component.searchTerm.set('xyz-not-found');
      fixture.detectChanges();

      expect(component.filteredLines().length).toBe(0);
    }));
  });

  describe('max line limit (5000)', () => {
    it('should keep at most 5000 lines', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      for (let i = 0; i < 5100; i++) {
        component.addLogLine(`line ${i}`);
      }
      fixture.detectChanges();

      expect(component.logLines().length).toBe(5000);
    }));

    it('should keep the most recent lines when over limit', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      for (let i = 0; i < 5001; i++) {
        component.addLogLine(`line ${i}`);
      }
      fixture.detectChanges();

      // The first line should have been dropped; last line should remain
      const lines = component.logLines();
      expect(lines[lines.length - 1]).toBe('line 5000');
      expect(lines[0]).toBe('line 1'); // line 0 was dropped
    }));
  });

  describe('download', () => {
    it('should expose a downloadLogs method', () => {
      const { fixture, component: dlComponent } = createComponent();
      fixture.detectChanges();
      expect(typeof dlComponent.downloadLogs).toBe('function');
    });
  });

  describe('virtual scroll', () => {
    it('should use VIRTUAL_SCROLL_ITEM_SIZE constant of 20px for line height', () => {
      expect(VIRTUAL_SCROLL_ITEM_SIZE).toBe(20);
    });

    it('should expose itemSize property matching VIRTUAL_SCROLL_ITEM_SIZE', () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      expect(component.itemSize).toBe(VIRTUAL_SCROLL_ITEM_SIZE);
    });

    it('should handle large datasets (5000 lines) without exceeding MAX_LINES', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      for (let i = 0; i < 5000; i++) {
        component.addLogLine(`log line ${i}`);
      }
      fixture.detectChanges();

      // Should cap at 5000 lines (MAX_LINES)
      expect(component.logLines().length).toBe(5000);
      // parsedLines computed should also have 5000 items
      expect(component.parsedLines().length).toBe(5000);
    }));

    it('should render cdk-virtual-scroll-viewport in the DOM when lines present', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      component.addLogLine('hello virtual scroll');
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('cdk-virtual-scroll-viewport')).toBeTruthy();
    }));
  });

  describe('ANSI rendering in DOM', () => {
    it('should render empty state when no lines', () => {
      const { fixture } = createComponent();
      fixture.detectChanges();
      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="run-log-empty"]')).toBeTruthy();
    });

    it('should render log lines when lines are present', fakeAsync(async () => {
      const { fixture, component } = createComponent();
      fixture.detectChanges();
      tick();

      component.addLogLine('test log line');
      fixture.detectChanges();

      const el: HTMLElement = fixture.nativeElement;
      expect(el.querySelector('[data-testid="run-log-content"]')).toBeTruthy();
      expect(el.textContent).toContain('test log line');
    }));
  });
});
