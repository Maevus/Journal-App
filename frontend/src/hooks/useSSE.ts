import { useCallback } from 'react'
import { synthesisUrl, type SynthesisScope } from '../api/client'
import { usePostStream } from './usePostStream'

interface UseSSEReturn {
  text: string
  error: string | null
  /** Message from an `event: quota` refusal frame (F2) — the backend refused
   *  the LLM call before spending. A refusal is an answer, not an error. */
  quota: string | null
  /** Message from an `event: rate_limited` refusal frame — refused for asking
   *  too fast rather than for being over budget. Rendered as a notice too. */
  rateLimited: string | null
  isStreaming: boolean
  start: (scope: SynthesisScope) => void
  abort: () => void
}

/**
 * Streams the synthesis over `POST /stream/synthesis`.
 *
 * This used to be an `EventSource` hook — hence the name — which made it a GET,
 * and a GET carrying the `SameSite=Lax` session cookie is reachable by
 * cross-site navigation: any page could spend a logged-in user's LLM budget (or
 * a BYO-key user's own key, since key-holders are quota-exempt). It now runs on
 * the shared `usePostStream` client alongside `useChatStream`; the scope that
 * used to be a query string is the POST body. The only stream-specific thing
 * left is which endpoint and what to send, so this hook registers no named-frame
 * handlers — the refusal frames are handled for every stream by the client.
 */
export function useSSE(): UseSSEReturn {
  const { text, error, quota, rateLimited, isStreaming, start: post, abort } = usePostStream()

  const start = useCallback(
    (scope: SynthesisScope) => {
      post(synthesisUrl(), scope)
    },
    [post],
  )

  return { text, error, quota, rateLimited, isStreaming, start, abort }
}
