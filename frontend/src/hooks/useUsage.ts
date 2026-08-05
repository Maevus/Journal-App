import { useEffect, useState } from 'react'
import { KEY_CHANGED_EVENT, fetchUsage, type UsageSummary } from '../api/client'

/** The logged-in user's LLM spend, refetched on KEY_CHANGED_EVENT — the tier
 *  chip and cap must not go stale while the key panel is on screen. */
export function useUsage(days = 30) {
  const [data, setData] = useState<UsageSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => {
      fetchUsage(days)
        .then((summary) => {
          if (!cancelled) {
            setData(summary)
            setError(null)
          }
        })
        .catch((e: Error) => {
          if (!cancelled) setError(e.message)
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }
    // No setLoading(true) here: `loading` starts true for the first fetch;
    // refetches keep the current card visible instead of flashing the skeleton.
    load()
    window.addEventListener(KEY_CHANGED_EVENT, load)
    return () => {
      cancelled = true
      window.removeEventListener(KEY_CHANGED_EVENT, load)
    }
  }, [days])

  return { data, loading, error }
}
