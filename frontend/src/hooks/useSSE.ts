import { useCallback, useRef, useState } from 'react'

interface UseSSEReturn {
  text: string
  error: string | null
  /** Message from an `event: quota` refusal frame (F2) — the backend refused
   *  the LLM call before spending. A refusal is an answer, not an error. */
  quota: string | null
  isStreaming: boolean
  start: () => void
  abort: () => void
}

export function useSSE(url: string): UseSSEReturn {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [quota, setQuota] = useState<string | null>(null)
  const [isStreaming, setIsStreaming] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)

  const abort = useCallback(() => {
    sourceRef.current?.close()
    sourceRef.current = null
    setIsStreaming(false)
  }, [])

  const start = useCallback(() => {
    sourceRef.current?.close()
    setText('')
    setError(null)
    setQuota(null)
    setIsStreaming(true)

    // withCredentials is EventSource's spelling of `credentials: 'include'` —
    // the synthesis stream is auth-gated, so the session cookie must ride along.
    const es = new EventSource(url, { withCredentials: true })
    sourceRef.current = es

    es.onmessage = (event: MessageEvent<string>) => {
      if (event.data === '[DONE]') {
        es.close()
        sourceRef.current = null
        setIsStreaming(false)
        return
      }
      try {
        // Backend JSON-encodes each token to safely carry newlines over SSE
        const token = JSON.parse(event.data) as string
        setText((prev) => prev + token)
      } catch {
        setText((prev) => prev + event.data)
      }
    }

    // Named events bypass onmessage — the quota refusal frame needs its own
    // listener. The frame is followed by a normal [DONE], which closes us.
    es.addEventListener('quota', (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as { message?: string }
        setQuota(payload.message ?? 'Daily LLM budget reached.')
      } catch {
        setQuota('Daily LLM budget reached.')
      }
    })

    es.onerror = () => {
      es.close()
      sourceRef.current = null
      setIsStreaming(false)
      setError('Stream connection failed.')
    }
  }, [url])

  return { text, error, quota, isStreaming, start, abort }
}
