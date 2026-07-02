import { loginUrl } from '../api/client'
import { useAuth } from '../hooks/useAuth'

/** Header login state: "Sign in with Google" (a plain link — the OAuth flow
 *  is a full-page redirect chain, not an XHR) or the signed-in email with a
 *  sign-out button. Renders nothing while the /auth/me probe is in flight so
 *  the header doesn't flash the wrong state. */
export function AuthWidget() {
  const { user, loading, signOut } = useAuth()

  if (loading) return null

  if (!user) {
    return (
      <a href={loginUrl()} className="btn-brutal bg-white px-2 py-0.5 text-xs">
        Sign in with Google
      </a>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-ink/60">{user.email}</span>
      <button
        onClick={() => void signOut()}
        className="btn-brutal bg-white px-2 py-0.5 text-xs"
      >
        Sign out
      </button>
    </div>
  )
}
