import { useCallback, useRef, useState } from 'react'
import {
  chatStreamUrl,
  reportIfAuthExpired,
  type ChatMessage,
  type Citation,
} from '../api/client'

interface UseChatStreamReturn {
  text: string
  sources: Citation[]
  error: string | null
  /** Message from an `event: quota` refusal frame (F2) — the backend refused
   *  the LLM call before spending. A refusal is an answer, not an error. */
  quota: string | null
  isStreaming: boolean
  start: (messages: ChatMessage[], limit?: number) => void
  abort: () => void
}

/**
 * Streams a multi-turn chat answer over POST (EventSource can't POST, and we
 * must carry the client-held conversation each turn). Parses the SSE frames by
 * hand off a fetch ReadableStream: `data:` frames carry JSON-encoded text
 * tokens; a single `event: sources` frame carries the retrieved entries for
 * citation chips; `data: [DONE]` ends the turn.
 */
export function useChatStream(): UseChatStreamReturn {
  const [text, setText] = useState('')
  const [sources, setSources] = useState<Citation[]>([])
  const [error, setError] = useState<string | null>(null)
  const [quota, setQuota] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const ctrlRef = useRef<AbortController | null>(null)

  const abort = useCallback(() => {
    ctrlRef.current?.abort()
    ctrlRef.current = null
    setIsStreaming(false)
  }, [])

  const start = useCallback((messages: ChatMessage[], limit = 8) => {
    ctrlRef.current?.abort()
    const ctrl = new AbortController()
    ctrlRef.current = ctrl
    setText('')
    setSources([])
    setError(null)
    setQuota(null)
    setIsStreaming(true)

    const handleFrame = (frame: string) => {
      // A frame may carry an `event:` line plus one or more `data:` lines.
      let event = 'message'
      const dataLines: string[] = []
      for (const line of frame.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
      }
      const data = dataLines.join('\n')
      if (event === 'sources') {
        try {
          setSources(JSON.parse(data) as Citation[])
        } catch {
          /* malformed sources frame — leave chips empty */
        }
        return
      }
      if (event === 'quota') {
        // The F2 refusal: the backend declined the LLM call before spending.
        try {
          const payload = JSON.parse(data) as { message?: string }
          setQuota(payload.message ?? 'Daily LLM budget reached.')
        } catch {
          setQuota('Daily LLM budget reached.')
        }
        return
      }
      if (data === '[DONE]') return // stream ends on its own; nothing to render
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
        const res = await fetch(chatStreamUrl(), {
          method: 'POST',
          credentials: 'include', // chat is auth-gated: retrieval is scoped to the session's user
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ messages, limit }),
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
        if (ctrlRef.current === ctrl) {
          ctrlRef.current = null
          setIsStreaming(false)
        }
      }
    })()
  }, [])

  return { text, sources, error, quota, isStreaming, start, abort }
}
