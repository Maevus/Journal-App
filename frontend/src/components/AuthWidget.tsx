import type { AuthUser } from '../api/client'

/** Signed-in header state: the email plus a sign-out button. Purely
 *  presentational — App owns useAuth and only renders this when a user
 *  exists; the logged-out state is the full-screen LoginGate.
 *
 *  Demo mode shows a "Demo" badge and NO sign-out: the demo is a shared
 *  showcase account, not a real login (get_current_user hard-resolves to it
 *  whenever IS_DEMO). Offering sign-out there nulled the user → dropped onto
 *  the LoginGate → whose "Sign in with Google" can only error or be ignored.
 *  Removing the button keeps LoginGate's "never shown in demo" invariant true. */
export function AuthWidget({ user, onSignOut }: { user: AuthUser; onSignOut: () => void }) {
  if (user.demo) {
    return (
      <span className="rounded-brutal border-2 border-ink bg-gold px-2 py-0.5 text-xs font-bold uppercase tracking-wide">
        Demo mode
      </span>
    )
  }
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-ink/60">{user.email}</span>
      <button onClick={onSignOut} className="btn-brutal bg-white px-2 py-0.5 text-xs">
        Sign out
      </button>
    </div>
  )
}
