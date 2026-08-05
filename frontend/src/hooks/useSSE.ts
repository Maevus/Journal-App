import { useCallback } from 'react'
import { synthesisUrl, type SynthesisScope } from '../api/client'
import { usePostStream } from './usePostStream'

interface UseSSEReturn {
  text: string
  error: string | null
  /** Message from an `event: quota` refusal frame: the backend refused
   *  before spending. A refusal is an answer, not an error. */
  quota: string | null
  /** Message from an `event: rate_limited` refusal frame — refused for asking
   *  too fast rather than for being over budget. Rendered as a notice too. */
  rateLimited: string | null
  isStreaming: boolean
  start: (scope: SynthesisScope) => void
  abort: () => void
}

/**
 * Streams the synthesis over `POST /stream/synthesis`. POST, with the scope
 * in the body, keeps the `SameSite=Lax` session cookie from being spendable
 * via a cross-site GET. Registers no named-frame handlers — refusal frames
 * are handled for every stream by `usePostStream`.
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
