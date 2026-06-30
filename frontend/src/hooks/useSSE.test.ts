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
})