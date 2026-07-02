import { useCallback, useEffect, useState } from 'react'
import { fetchMe, logout, type AuthUser } from '../api/client'

/** App-wide login state: probe /auth/me once on mount. A failed probe reads
 *  as logged-out rather than an error — login is optional until the isolation
 *  phase gates the data routes, so auth trouble must never block the app. */
export function useAuth() {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    fetchMe()
      .then((u) => {
        if (!cancelled) setUser(u)
      })
      .catch(() => {
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const signOut = useCallback(async () => {
    await logout() // revoke server-side first; only then forget locally
    setUser(null)
  }, [])

  return { user, loading, signOut }
}
