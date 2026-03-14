// Set fakeAsync patch lock BEFORE zone.js is loaded
// This must be set before zone.js is loaded for fakeAsync to work
// biome-ignore lint/suspicious/noExplicitAny: zone.js uses global symbols
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- zone.js uses global symbols
(window as any).__zone_symbol__fakeAsyncPatchLock = true;

// Import zone.js first to define the Zone global
import 'zone.js';
// Then import zone.js/testing to add fakeAsync support
import 'zone.js/testing';
import { TestBed } from '@angular/core/testing';
import { BrowserDynamicTestingModule, platformBrowserDynamicTesting } from '@angular/platform-browser-dynamic/testing';

// Only initialize if not already initialized (prevents NG0400 error)
try {
  TestBed.initTestEnvironment(BrowserDynamicTestingModule, platformBrowserDynamicTesting());
} catch {
  // Platform already initialized, this is expected in some test environments
}

const originalWarn = console.warn;

console.warn = function (...args: unknown[]) {
  const firstArg = args[0];
  if (
    typeof firstArg === 'string' &&
    (firstArg.includes("'allowSignalWrites' flag is deprecated") ||
     firstArg.includes("'NgZone' is deprecated") ||
     firstArg.includes("The application is using zoneless change detection"))
  ) {
    return;
  }

  return originalWarn.apply(console, args);
};
