import { useCallback, useEffect, useState } from 'react'
import { AUTH_EXPIRED_EVENT, fetchMe, logout, type AuthUser } from '../api/client'

/** App-wide login state: probe /auth/me once on mount, and listen for the
 *  session dying mid-use (any data route answering 401 dispatches
 *  AUTH_EXPIRED_EVENT). App owns the single instance and gates on it — every
 *  data route is auth-scoped, so `user == null` means the login gate IS the
 *  app. A failed probe reads as logged-out rather than an error. */
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
