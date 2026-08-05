import { useCallback, useEffect, useState } from 'react'
import { AUTH_EXPIRED_EVENT, fetchMe, logout, type AuthUser } from '../api/client'

/** App-wide login state: probes /auth/me once on mount, then listens for
 *  AUTH_EXPIRED_EVENT (any data route's 401). A failed probe reads as logged-out, not an error. */
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

  useEffect(() => {
    const onExpired = () => setUser(null)
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired)
  }, [])

  const signOut = useCallback(async () => {
    await logout() // revoke server-side first; only then forget locally
    setUser(null)
  }, [])

  return { user, loading, signOut }
}
