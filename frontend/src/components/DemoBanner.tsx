import { loginUrl } from '../api/client'

/** Top strip for the signed-out demo: an anonymous visitor is resolved to
 *  the read-only demo journal; this explains that and offers sign-in. Shown only when `user.demo` is true. */
export function DemoBanner() {
  return (
    <div className="border-b-4 border-ink bg-lilac px-4 py-2.5 text-white sm:px-6">
      <div className="mx-auto flex max-w-7xl flex-col items-start gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm font-medium">
          <span className="font-bold uppercase tracking-wide">Demo</span>
          {' — you’re exploring a sample journal. It’s read-only; sign in to start your own.'}
        </p>
        <a
          href={loginUrl()}
          className="btn-brutal shrink-0 bg-white px-3 py-1 text-sm text-ink"
        >
          Sign in with Google
        </a>
      </div>
    </div>
  )
}
