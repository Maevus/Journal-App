import { useCallback, useRef, useState } from 'react'
import { reportIfAuthExpired } from '../api/client'

/**
 * Shared SSE client for both LLM streams: POST JSON, read the response as a
 * byte stream, turn `data:`/`event:` frames into React state.
 *
 * POST avoids a cross-site-reachable GET (paid call + `SameSite=Lax` cookie);
 * `fetch`+`AbortController` (not `EventSource`) makes Stop actually hang up;
 * `reportIfAuthExpired` sends a mid-stream 401 to the login gate.
 */

/** Handlers for stream-specific named frames, keyed by `event:` name (chat
 *  registers `sources`; synthesis registers none). `quota`/`rate_limited`/
 *  `llm_error` are handled here for everyone. Pass a stable object — it feeds
 *  `start`'s deps. */
export type FrameHandlers = Record<string, (data: string) => void>

/** Module-level so the default argument is referentially stable across renders. */
const NO_HANDLERS: FrameHandlers = {}

export interface UsePostStreamReturn {
  text: string
  error: string | null
  /** Message from an `event: quota` refusal frame: the backend refused
   *  before spending. A refusal is an answer, not an error. */
  quota: string | null
  /** Message from an `event: rate_limited` refusal frame: caller is asking
   *  too fast. Same shape as `quota` — a notice, not an error. */
  rateLimited: string | null
  isStreaming: boolean
  start: (url: string, body: unknown) => void
  abort: () => void
}

/** One SSE frame's `event:` name and `data:` payload. Multiple `data:` lines
 *  join with newlines (SSE spec); no `event:` line means the default, `message`. */
function parseFrame(frame: string): { event: string; data: string } {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  return { event, data: dataLines.join('\n') }
}

/** Human-readable line for a refusal or llm_error frame. All carry
 *  `{code, message, …}`; prefer the server's `message` — it knows the
 *  budget/window/retry delay, or what the provider said. */
function refusalMessage(data: string, fallback: string): string {
  try {
    const payload = JSON.parse(data) as { message?: string; retry_after?: number }
    if (payload.message) return payload.message
    // No message, but the wire contract promises a retry delay: say something
    // more useful than the generic line.
    if (typeof payload.retry_after === 'number') {
      return `Too many requests — try again in ${payload.retry_after}s.`
    }
  } catch {
    /* malformed refusal frame — the fallback still tells the user what happened */
  }
  return fallback
}

export function usePostStream(handlers: FrameHandlers = NO_HANDLERS): UsePostStreamReturn {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [quota, setQuota] = useState<string | null>(null)
  const [rateLimited, setRateLimited] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const ctrlRef = useRef<AbortController | null>(null)

  const abort = useCallback(() => {
    ctrlRef.current?.abort()
    ctrlRef.current = null
    setIsStreaming(false)
  }, [])

  const start = useCallback(
    (url: string, body: unknown) => {
      ctrlRef.current?.abort()
      const ctrl = new AbortController()
      ctrlRef.current = ctrl
      setText('')
      setError(null)
      setQuota(null)
      setRateLimited(null)
      setIsStreaming(true)

      const handleFrame = (frame: string) => {
        const { event, data } = parseFrame(frame)
        if (event === 'quota') {
          setQuota(refusalMessage(data, 'Daily LLM budget reached.'))
          return
        }
        if (event === 'rate_limited') {
          setRateLimited(refusalMessage(data, 'Too many requests — try again shortly.'))
          return
        }
        // Unlike quota/rate_limited, this one is a failure, not a refusal —
        // it lands in `error` alongside a dropped connection.
        if (event === 'llm_error') {
          setError(refusalMessage(data, 'The analysis service is unavailable right now.'))
          return
        }
        const handler = handlers[event]
        if (handler) {
          handler(data)
          return
        }
        // An unrecognised named frame is a protocol addition we predate: skip
        // it rather than rendering its JSON into the prose.
        if (event !== 'message') return
        if (data === '[DONE]') return // the stream ends on its own; nothing to render
        try {
          // Tokens are JSON-encoded so newlines survive the SSE frame.
          const token = JSON.parse(data) as string
          setText((prev) => prev + token)
        } catch {
          setText((prev) => prev + data)
        }
      }

      void (async () => {
        try {
          const res = await fetch(url, {
            method: 'POST',
            credentials: 'include', // both streams are auth-gated and user-scoped
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: ctrl.signal,
          })
          reportIfAuthExpired(res)
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
          const reader = res.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          for (;;) {
            const { done, value } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            // Frames are `\n\n`-delimited and a read() can split one anywhere,
            // so only complete frames leave the buffer.
            let sep: number
            while ((sep = buffer.indexOf('\n\n')) !== -1) {
              handleFrame(buffer.slice(0, sep))
              buffer = buffer.slice(sep + 2)
            }
          }
        } catch (e) {
          if ((e as Error).name === 'AbortError') return // user pressed Stop
          setError('Stream connection failed — is the backend running?')
        } finally {
          // Only the newest stream owns the flag: a start() that superseded this
          // one has already set its own controller and must not be switched off.
          if (ctrlRef.current === ctrl) {
            ctrlRef.current = null
            setIsStreaming(false)
          }
        }
      })()
    },
    [handlers],
  )

  return { text, error, quota, rateLimited, isStreaming, start, abort }
}
