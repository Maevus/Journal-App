import { useCallback, useMemo, useState } from 'react'
import { chatStreamUrl, type ChatMessage, type Citation } from '../api/client'
import { usePostStream } from './usePostStream'

interface UseChatStreamReturn {
  text: string
  sources: Citation[]
  error: string | null
  /** Message from an `event: quota` refusal frame (F2) — the backend refused
   *  the LLM call before spending. A refusal is an answer, not an error. */
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
 * client-held conversation each turn (the server stays stateless).
 *
 * The transport lives in `usePostStream`, shared with the synthesis stream. All
 * that's chat-specific is the endpoint, the body, and one named frame: a single
 * `event: sources` carrying the retrieved entries for the citation chips
 * (ADR 0002), which arrives after the text and before `[DONE]`.
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
      // The shared client resets the text/error/refusal state; the citations are
      // this hook's own, so clear them here — stale chips must not outlive the
      // answer they belonged to.
      setSources([])
      post(chatStreamUrl(), { messages, limit })
    },
    [post],
  )

  return { text, sources, error, quota, rateLimited, isStreaming, start, abort }
}
