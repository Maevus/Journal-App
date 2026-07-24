import { loginUrl } from '../api/client'

/** Top strip for the signed-out demo (hosting shape B): a visitor who lands
 *  without an account is resolved to the seeded demo user and sees a read-only
 *  sample journal. This explains that and offers the way out — sign in with
 *  Google to start their own. App renders it only when `user.demo` is true. */
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
