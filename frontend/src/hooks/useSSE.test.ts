import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSSE } from './useSSE'

// Minimal EventSource stand-in (jsdom has none). Captures instances so tests
// can drive onmessage / onerror.
class FakeEventSource {
  static instances: FakeEventSource[] = []
  url: string
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  close = vi.fn()
  // Named SSE events (e.g. `event: quota`) never reach onmessage — they only
  // fire listeners registered for that exact type, like the real EventSource.
  listeners: Record<string, (e: { data: string }) => void> = {}
  addEventListener = (type: string, fn: (e: { data: string }) => void) => {
    this.listeners[type] = fn
  }

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  static last() {
    return this.instances[this.instances.length - 1]
  }
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useSSE', () => {
  it('opens an EventSource on start and marks streaming', () => {
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    expect(FakeEventSource.last().url).toBe('http://x/stream')
    expect(result.current.isStreaming).toBe(true)
  })

  it('appends JSON-decoded tokens to text', () => {
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    act(() => FakeEventSource.last().onmessage?.({ data: '"Hello"' }))
    act(() => FakeEventSource.last().onmessage?.({ data: '" world"' }))
    expect(result.current.text).toBe('Hello world')
  })

  it('closes and stops streaming on [DONE]', () => {
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    const es = FakeEventSource.last()
    act(() => es.onmessage?.({ data: '[DONE]' }))
    expect(es.close).toHaveBeenCalled()
    expect(result.current.isStreaming).toBe(false)
  })

  it('abort closes the connection', () => {
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    const es = FakeEventSource.last()
    act(() => result.current.abort())
    expect(es.close).toHaveBeenCalled()
    expect(result.current.isStreaming).toBe(false)
  })

  it('surfaces an error on connection failure', () => {
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    act(() => FakeEventSource.last().onerror?.())
    expect(result.current.error).toMatch(/Stream connection failed/i)
    expect(result.current.isStreaming).toBe(false)
  })

  it('captures a quota refusal as quota, not error', () => {
    // The backend refuses over-budget calls inside the 200 stream with a named
    // `event: quota` frame (F2) — a refusal is an answer, not a failure.
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    act(() =>
      FakeEventSource.last().listeners['quota']?.({
        data: '{"code":"quota_exhausted","message":"daily LLM budget exhausted"}',
      }),
    )
    act(() => FakeEventSource.last().onmessage?.({ data: '[DONE]' })) // frame is followed by a clean DONE
    expect(result.current.quota).toMatch(/budget/i)
    expect(result.current.error).toBeNull()
    expect(result.current.isStreaming).toBe(false)
  })

  it('start() clears a previous quota notice', () => {
    const { result } = renderHook(() => useSSE('http://x/stream'))
    act(() => result.current.start())
    act(() =>
      FakeEventSource.last().listeners['quota']?.({
        data: '{"code":"quota_exhausted","message":"daily LLM budget exhausted"}',
      }),
    )
    expect(result.current.quota).not.toBeNull()
    act(() => result.current.start())
    expect(result.current.quota).toBeNull()
  })
})