import { useCallback, useMemo, useState } from 'react'
import { chatStreamUrl, type ChatMessage, type Citation } from '../api/client'
import { usePostStream } from './usePostStream'

interface UseChatStreamReturn {
  text: string
  sources: Citation[]
  error: string | null
  /** Message from an `event: quota` refusal frame: the backend refused
   *  before spending. A refusal is an answer, not an error. */
  quota: string | null
  /** Message from an `event: rate_limited` refusal frame — refused for asking
   *  too fast rather than for being over budget. Rendered as a notice too. */
  rateLimited: string | null
  isStreaming: boolean
  start: (messages: ChatMessage[], limit?: number) => void
  abort: () => void
}

/**
 * Streams a multi-turn chat answer over `POST /stream/chat`, carrying the
 * client-held conversation each turn (server stays stateless). Transport is
 * shared `usePostStream`; chat only adds the endpoint, body, and the
 * `event: sources` frame (ADR 0002) — it arrives after the text, before `[DONE]`.
 */
export function useChatStream(): UseChatStreamReturn {
  const [sources, setSources] = useState<Citation[]>([])

  // Memoised because the handler map feeds usePostStream's `start` dependency
  // list; `setSources` is itself stable, so this never needs to change.
  const handlers = useMemo(
    () => ({
      sources: (data: string) => {
        try {
          setSources(JSON.parse(data) as Citation[])
        } catch {
          /* malformed sources frame — leave chips empty */
        }
      },
    }),
    [],
  )

  const { text, error, quota, rateLimited, isStreaming, start: post, abort } =
    usePostStream(handlers)

  const start = useCallback(
    (messages: ChatMessage[], limit = 8) => {
      // The shared client resets text/error/refusal; citations are this
      // hook's own state, so clear them here too — stale chips must not outlive the answer.
      setSources([])
      post(chatStreamUrl(), { messages, limit })
    },
    [post],
  )

  return { text, sources, error, quota, rateLimited, isStreaming, start, abort }
}
