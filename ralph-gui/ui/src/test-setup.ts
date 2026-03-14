// This file is required by karma.conf.js and is loaded before running tests
import 'zone.js/testing';
import { getTestBed } from '@angular/core/testing';
import {
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting,
} from '@angular/platform-browser-dynamic/testing';

// Suppress known, intentional test-environment warnings.
// NG0914: The app uses provideZonelessChangeDetection() which emits this warning
// when Zone.js is also loaded (Zone.js is required for fakeAsync/tick test utilities).
// This is an expected configuration in zoneless Angular apps using Karma for testing.
//
// allowSignalWrites: Deprecated flag used in some effect() calls in older test files.
// The flag is harmless but creates noise in test output.
const _originalWarn = console.warn;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(console as any).warn = function (...args: unknown[]) {
  const firstArg = args[0];
  if (
    typeof firstArg === 'string' &&
    (firstArg.includes('NG0914') || firstArg.includes('allowSignalWrites'))
  ) {
    return;
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (_originalWarn as any).apply(console, args);
};

// Initialize the Angular testing environment
getTestBed().initTestEnvironment(
  BrowserDynamicTestingModule,
  platformBrowserDynamicTesting(),
  { teardown: { destroyAfterEach: true } },
);
