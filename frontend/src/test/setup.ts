// Registers jest-dom matchers (e.g. toBeInTheDocument) on Vitest's expect.
import '@testing-library/jest-dom/vitest'

// jsdom lacks ResizeObserver, which recharts' ResponsiveContainer needs.
// This no-op stub lets chart components render at 0×0 — assert on text, not SVG.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver

