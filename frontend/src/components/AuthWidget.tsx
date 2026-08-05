import type { AuthUser } from '../api/client'

/** Signed-in header state: email + sign-out button; LoginGate covers the
 *  logged-out state. Demo mode shows a "Demo" badge instead, since it's a
 *  shared account, not a real login, with nowhere useful for sign-out to send you. */
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
