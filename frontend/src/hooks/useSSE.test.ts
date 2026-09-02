import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AUTH_EXPIRED_EVENT } from '../api/client'
import { useSSE } from './useSSE'

// POST + fetch, driven by a ReadableStream body — the same harness useChatStream uses.
function streamResponse(chunks: string[], ok = true, status = 200): Response {
  const enc = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
  return { ok, status, body } as unknown as Response
}

const scope = { limit: 20, days: 30, source: null }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useSSE', () => {
  it('POSTs the scope to the synthesis endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(streamResponse(['data: [DONE]\n\n']))
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start({ limit: 10, days: 7, source: 'google_keep' }))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('http://localhost:8000/stream/synthesis')
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual({ limit: 10, days: 7, source: 'google_keep' })
  })

  it('accumulates JSON-decoded tokens and stops streaming on [DONE]', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse(['data: "Hello"\n\n', 'data: " world"\n\n', 'data: [DONE]\n\n']),
      ),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.text).toBe('Hello world') // and no literal "[DONE]" in it
    expect(result.current.error).toBeNull()
  })

  it('reassembles a frame split across read() boundaries', async () => {
    // A read() can split a frame anywhere, including mid-value — the buffer
    // must hold the partial frame rather than parsing it.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(streamResponse(['data: "Hel', 'lo"\n\n', 'data: [DONE]\n\n'])),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.text).toBe('Hello')
  })

  it('captures a quota refusal as quota, not error or text', async () => {
    // Over-budget runs are refused inside the 200 stream: a named
    // `event: quota` frame, no tokens, then a clean [DONE].
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          'event: quota\ndata: {"code":"quota_exhausted","message":"daily LLM budget exhausted"}\n\n',
          'data: [DONE]\n\n',
        ]),
      ),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.quota).toBe('daily LLM budget exhausted') // the server's wording
    expect(result.current.text).toBe('')
    expect(result.current.error).toBeNull()
  })

  it('captures a rate_limited refusal as its own notice, not an error', async () => {
    // The rate limiter refuses the same way quota does — inside the 200 stream,
    // so it must not surface as the red "connection failed" banner.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          'event: rate_limited\ndata: {"code":"rate_limited","message":"Too many requests. Try again in 30 seconds.","retry_after":30}\n\n',
          'data: [DONE]\n\n',
        ]),
      ),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.rateLimited).toBe('Too many requests. Try again in 30 seconds.')
    expect(result.current.quota).toBeNull()
    expect(result.current.error).toBeNull()
    expect(result.current.text).toBe('')
  })

  it('falls back to retry_after when a rate_limited frame carries no message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          'event: rate_limited\ndata: {"code":"rate_limited","retry_after":45}\n\n',
          'data: [DONE]\n\n',
        ]),
      ),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.rateLimited).toMatch(/45s/)
  })

  it('surfaces an llm_error frame as an error, not a refusal notice', async () => {
    // The provider failed after the call was allowed — unlike quota, that is a
    // failure, so it lands in `error` and leaves the refusal states clear.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        streamResponse([
          'event: llm_error\ndata: {"code":"provider_error","message":"The analysis service is unavailable right now."}\n\n',
          'data: [DONE]\n\n',
        ]),
      ),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.error).toBe('The analysis service is unavailable right now.')
    expect(result.current.quota).toBeNull()
    expect(result.current.rateLimited).toBeNull()
    expect(result.current.text).toBe('')
  })

  it('start() clears a previous refusal notice', async () => {
    const refusal = streamResponse([
      'event: quota\ndata: {"code":"quota_exhausted","message":"daily LLM budget exhausted"}\n\n',
      'data: [DONE]\n\n',
    ])
    const answer = streamResponse(['data: "Hi"\n\n', 'data: [DONE]\n\n'])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(refusal).mockResolvedValueOnce(answer))

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))
    await waitFor(() => expect(result.current.quota).not.toBeNull())

    act(() => result.current.start(scope))
    await waitFor(() => expect(result.current.text).toBe('Hi'))
    expect(result.current.quota).toBeNull()
  })

  it('abort stops the stream', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {}))) // never resolves
    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))
    expect(result.current.isStreaming).toBe(true)
    act(() => result.current.abort())
    expect(result.current.isStreaming).toBe(false)
    expect(result.current.error).toBeNull() // stopping on purpose is not a failure
  })

  it('aborts mid-stream after some tokens have arrived', async () => {
    const enc = new TextEncoder()
    // Emits one token then stays open — the abort has to hang up on the wire.
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode('data: "Hi"\n\n'))
      },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, status: 200, body } as unknown as Response),
    )

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(result.current.text).toBe('Hi'))
    expect(result.current.isStreaming).toBe(true)
    act(() => result.current.abort())
    expect(result.current.isStreaming).toBe(false)
  })

  it('surfaces an error on a non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse([], false, 500)))
    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))
    await waitFor(() => expect(result.current.error).toMatch(/backend running/i))
    expect(result.current.isStreaming).toBe(false)
  })

  it('reports an expired session so the app can show the login gate', async () => {
    // A 401 mid-stream dispatches AUTH_EXPIRED_EVENT instead of surfacing as a
    // connection failure.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse([], false, 401)))
    const expired = vi.fn()
    window.addEventListener(AUTH_EXPIRED_EVENT, expired)

    const { result } = renderHook(() => useSSE())
    act(() => result.current.start(scope))

    await waitFor(() => expect(expired).toHaveBeenCalled())
    expect(result.current.isStreaming).toBe(false)
    window.removeEventListener(AUTH_EXPIRED_EVENT, expired)
  })
})
