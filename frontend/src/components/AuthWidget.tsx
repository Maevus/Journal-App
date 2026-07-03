import type { AuthUser } from '../api/client'

/** Signed-in header state: the email plus a sign-out button. Purely
 *  presentational — App owns useAuth and only renders this when a user
 *  exists; the logged-out state is the full-screen LoginGate. */
export function AuthWidget({ user, onSignOut }: { user: AuthUser; onSignOut: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-ink/60">{user.email}</span>
      <button onClick={onSignOut} className="btn-brutal bg-white px-2 py-0.5 text-xs">
        Sign out
      </button>
    </div>
  )
}
