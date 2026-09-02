import { loginUrl } from '../api/client'

/** Full-screen logged-out state, and the front door of a private instance:
 *  with PUBLIC_DEMO off every data route 401s, so this gate IS the app. Under
 *  PUBLIC_DEMO a signed-out visitor gets the demo instead and never sees this. */
export function LoginGate() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-paper font-sans text-ink">
      <div className="card-brutal relative max-w-md p-8 pt-10 text-center">
        <h1 className="font-display text-3xl font-bold uppercase tracking-tight">
          Journal <span className="inline-block -rotate-1 bg-rose px-2">Analytics</span>
        </h1>
        <p className="mt-4 text-sm font-medium text-ink/60">
          Sign in to see your entries, trends, and
          synthesis.
        </p>
        <a
          href={loginUrl()}
          className="btn-brutal mt-6 inline-block bg-white px-4 py-2 text-sm"
        >
          Sign in with Google
        </a>
      </div>
    </div>
  )
}
