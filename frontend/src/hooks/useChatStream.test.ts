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
})
