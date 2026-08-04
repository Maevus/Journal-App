import { useCallback, useRef, useState } from 'react'
import { reportIfAuthExpired } from '../api/client'

/**
 * The SSE client both LLM streams run on: POST a JSON body, read the response
 * as a byte stream, and turn `data:`/`event:` frames into React state.
 *
 * Both streams are POST because a paid LLM call must not be reachable by
 * cross-site navigation: the session cookie is `SameSite=Lax`, so the browser
 * sends it on a top-level cross-site GET, and CORS only stops a third party
 * *reading* the response — not the server processing (and paying for) the
 * request. A POST with `Content-Type: application/json` needs a CORS preflight,
 * which the backend's origin allow-list refuses. Chat needed POST anyway (it
 * carries the conversation); synthesis was moved onto it to close that hole,
 * which is what let the two hooks collapse onto this one client.
 *
 * The cost of leaving `EventSource` behind is its auto-reconnect — but the old
 * `useSSE` closed the connection in `onerror` anyway, so nothing regresses. The
 * gains: a real `AbortController` on the wire (Stop actually hangs up, instead
 * of the server streaming into a closed reader), and `reportIfAuthExpired`, so
 * a session that dies mid-stream flips the app to the login gate instead of
 * surfacing as "Stream connection failed".
 */

/** Handlers for stream-specific named frames, keyed by the `event:` name.
 *  Chat registers `sources`; synthesis registers none. The refusal frames
 *  (`quota`, `rate_limited`) are handled here for everyone — they are protocol,
 *  not per-stream payload. Pass a stable (module-level or memoised) object:
 *  it feeds `start`'s dependency list. */
export type FrameHandlers = Record<string, (data: string) => void>

/** Module-level so the default argument is referentially stable across renders. */
const NO_HANDLERS: FrameHandlers = {}

export interface UsePostStreamReturn {
  text: string
  error: string | null
  /** Message from an `event: quota` refusal frame (F2) — the backend refused
   *  the LLM call before spending. A refusal is an answer, not an error. */
  quota: string | null
  /** Message from an `event: rate_limited` refusal frame — the backend refused
   *  because the caller is asking too fast. Same shape as `quota`: a notice,
   *  not the red error banner. */
  rateLimited: string | null
  isStreaming: boolean
  start: (url: string, body: unknown) => void
  abort: () => void
}

/** One SSE frame's `event:` name and its `data:` payload. A frame may carry an
 *  `event:` line plus one or more `data:` lines; multiple data lines join with
 *  newlines, per the SSE spec. No `event:` line means the default, `message`. */
function parseFrame(frame: string): { event: string; data: string } {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  return { event, data: dataLines.join('\n') }
}

/** The human-readable line for a refusal frame. Both refusal frames carry
 *  `{code, message, …}` and the server's `message` is the better copy — it
 *  knows the budget, the window, and the retry delay — so prefer it and fall
 *  back only when the payload is missing or malformed. */
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
