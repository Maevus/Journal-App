// Registers @testing-library/jest-dom matchers (e.g. toBeInTheDocument) on
// Vitest's expect, and their TypeScript types.
import '@testing-library/jest-dom/vitest'

// jsdom has no ResizeObserver; recharts' ResponsiveContainer requires one.
// A no-op stub lets chart-bearing components (UsagePanel, MoodChart) render
// in tests — the chart itself stays 0×0, so tests assert on text, not SVG.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

