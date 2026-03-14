const originalWarn = console.warn;

console.warn = function (...args: unknown[]) {
  const firstArg = args[0];
  if (
    typeof firstArg === 'string' &&
    firstArg.includes("'allowSignalWrites' flag is deprecated")
  ) {
    return;
  }

  return originalWarn.apply(console, args as Parameters<typeof originalWarn>);
};
