import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useChatStream } from './useChatStream'

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

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useChatStream', () => {
  it('accumulates text tokens and captures the sources frame', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        'data: "Hello"\n\n',
        'data: " world [1]"\n\n',
        'event: sources\ndata: [{"n":1,"id":5,"date":"2026-05-10","source":"text","snippet":"hi"}]\n\n',
        'data: [DONE]\n\n',
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.text).toBe('Hello world [1]')
    expect(result.current.sources).toEqual([
      { n: 1, id: 5, date: '2026-05-10', source: 'text', snippet: 'hi' },
    ])
    // POSTs the conversation to the chat endpoint
    const [, init] = fetchMock.mock.calls[0]
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({
      messages: [{ role: 'user', content: 'hi' }],
      limit: 8,
    })
  })

  it('reassembles a frame split across read() boundaries', async () => {
    // The token frame `data: "Hello"\n\n` is delivered in two chunks that split
    // it mid-value — the buffer must stitch them back together.
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse(['data: "Hel', 'lo"\n\n', 'data: [DONE]\n\n']),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.text).toBe('Hello')
  })

  it('aborts mid-stream after some tokens have arrived', async () => {
    const enc = new TextEncoder()
    // A stream that emits one token then stays open (never closes).
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(enc.encode('data: "Hi"\n\n'))
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, body } as unknown as Response))

    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))

    await waitFor(() => expect(result.current.text).toBe('Hi'))
    expect(result.current.isStreaming).toBe(true)
    act(() => result.current.abort())
    expect(result.current.isStreaming).toBe(false)
  })

  it('stops streaming on abort', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(() => {}))) // never resolves
    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))
    expect(result.current.isStreaming).toBe(true)
    act(() => result.current.abort())
    expect(result.current.isStreaming).toBe(false)
  })

  it('surfaces an error on a non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamResponse([], false, 500)))
    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))
    await waitFor(() => expect(result.current.error).toMatch(/backend running/i))
    expect(result.current.isStreaming).toBe(false)
  })

  it('captures a quota refusal frame as quota, not error or text', async () => {
    // Over-budget turns are refused inside the 200 stream (F2): a named
    // `event: quota` frame, no tokens, no sources, then a clean [DONE].
    const fetchMock = vi.fn().mockResolvedValue(
      streamResponse([
        'event: quota\ndata: {"code":"quota_exhausted","message":"daily LLM budget exhausted"}\n\n',
        'data: [DONE]\n\n',
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))

    await waitFor(() => expect(result.current.isStreaming).toBe(false))
    expect(result.current.quota).toMatch(/budget/i)
    expect(result.current.text).toBe('')
    expect(result.current.error).toBeNull()
  })

  it('start() clears a previous quota notice', async () => {
    const refusal = streamResponse([
      'event: quota\ndata: {"code":"quota_exhausted","message":"daily LLM budget exhausted"}\n\n',
      'data: [DONE]\n\n',
    ])
    const answer = streamResponse(['data: "Hi"\n\n', 'data: [DONE]\n\n'])
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(refusal).mockResolvedValueOnce(answer))

    const { result } = renderHook(() => useChatStream())
    act(() => result.current.start([{ role: 'user', content: 'hi' }]))
    await waitFor(() => expect(result.current.quota).not.toBeNull())

    act(() => result.current.start([{ role: 'user', content: 'again' }]))
    await waitFor(() => expect(result.current.text).toBe('Hi'))
    expect(result.current.quota).toBeNull()
  })
})
