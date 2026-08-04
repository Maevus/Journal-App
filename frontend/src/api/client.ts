const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

/** Fired when a data route answers 401: the session died mid-use (expired or
 *  revoked). useAuth listens and flips the app to the login gate — one emitter,
 *  one listener, instead of plumbing auth errors up through every hook.
 *  fetchMe deliberately does NOT report: its 401 means "logged out" as an
 *  answer, not an expiry. */
export const AUTH_EXPIRED_EVENT = 'journal:auth-expired'

export function reportIfAuthExpired(res: Response): Response {
  if (res.status === 401) window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
  return res
}

export interface SearchResult {
  id: number
  content: string
  source: string
  timestamp: string
  similarity_score: number
  external_id: string | null
}

/** Deep link to the original note at its source, or null if unavailable. */
export function sourceNoteUrl(source: string, externalId: string | null): string | null {
  if (source === 'google_keep' && externalId) {
    return `https://keep.google.com/u/0/#NOTE/${externalId}`
  }
  return null
}

export interface TrendPoint {
  date: string
  score: number
}

export interface DayEntry {
  id: number
  content: string
  source: string
  timestamp: string
  imported_at: string | null
  mood_score: number | null
  mood_reasoning: string | null
  external_id: string | null
}

export async function ingestEntries(
  entries: string[],
  source = 'text',
): Promise<{ ingested: number; skipped: number }> {
  const res = await fetch(`${BASE_URL}/ingest`, {
    method: 'POST',
    credentials: 'include', // /ingest is auth-gated: writes are stamped to their owner
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entries, source }),
  })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Ingest failed: ${res.statusText}`)
  return res.json() as Promise<{ ingested: number; skipped: number }>
}

export async function searchEntries(
  query: string,
  limit = 10,
): Promise<{ results: SearchResult[] }> {
  const res = await fetch(`${BASE_URL}/search`, {
    method: 'POST',
    credentials: 'include', // search is auth-gated and scoped to the session's user
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit }),
  })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Search failed: ${res.statusText}`)
  return res.json() as Promise<{ results: SearchResult[] }>
}

export async function fetchTrends(
  days = 30,
  source: string | null = null,
  start: string | null = null,
  end: string | null = null,
): Promise<TrendPoint[]> {
  // days=0 → all time; source=null → all sources. A start+end pair (ISO
  // "YYYY-MM-DD") is a custom window that overrides days server-side.
  const params = new URLSearchParams({ days: String(days) })
  if (source) params.set('source', source)
  if (start && end) {
    params.set('start', start)
    params.set('end', end)
  }
  const res = await fetch(`${BASE_URL}/trends?${params}`, { credentials: 'include' })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Trends failed: ${res.statusText}`)
  const data = (await res.json()) as { trends: TrendPoint[] }
  return data.trends
}

export async function fetchEntriesByDate(
  date: string,
  source: string | null = null,
): Promise<DayEntry[]> {
  // date: ISO "YYYY-MM-DD"; source=null → all sources
  const params = new URLSearchParams({ date })
  if (source) params.set('source', source)
  const res = await fetch(`${BASE_URL}/entries?${params}`, { credentials: 'include' })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Entries failed: ${res.statusText}`)
  const data = (await res.json()) as { entries: DayEntry[] }
  return data.entries
}

/** What a synthesis run covers, POSTed as the request body.
 *  `days: 0` → all time; `source: null` → all sources. */
export interface SynthesisScope {
  limit: number
  days: number
  source: string | null
}

/** Endpoint for the synthesis stream. POST, not GET, and the scope rides in the
 *  body rather than a query string: a GET carrying the `SameSite=Lax` session
 *  cookie is reachable by cross-site navigation, so any third-party page could
 *  trigger this paid LLM call. A JSON POST requires a CORS preflight, which the
 *  backend's origin allow-list refuses. Same bare-URL shape as chatStreamUrl. */
export function synthesisUrl(): string {
  return `${BASE_URL}/stream/synthesis`
}

/** One turn's role + text, as sent to / held by the multi-turn chat. */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

/** A retrieved entry the answer can cite, rendered as a clickable chip. */
export interface Citation {
  n: number
  id: number
  date: string
  source: string
  snippet: string
}

/** Endpoint for the multi-turn chat stream. POST the conversation; chat ignores
 *  the global range/source filters — a question defines its own scope. */
export function chatStreamUrl(): string {
  return `${BASE_URL}/stream/chat`
}

/** The logged-in identity, from GET /auth/me. `demo` means the shared demo
 *  account — key management is disabled and the tier chip reads "Demo". */
export interface AuthUser {
  id: number
  email: string
  demo: boolean
}

/** Where "Sign in with Google" points — a full-page navigation into the OAuth
 *  redirect chain (backend → Google → backend → back here), not a fetch. */
export function loginUrl(): string {
  return `${BASE_URL}/auth/login`
}

/** The logged-in user, or null when there's no live session. Auth calls send
 *  `credentials: 'include'` so the httpOnly session cookie rides along on the
 *  cross-origin request — without it the browser strips the cookie. */
export async function fetchMe(): Promise<AuthUser | null> {
  const res = await fetch(`${BASE_URL}/auth/me`, { credentials: 'include' })
  if (res.status === 401) return null // logged out — an answer, not an error
  if (!res.ok) throw new Error(`Auth check failed: ${res.statusText}`)
  return res.json() as Promise<AuthUser>
}

export async function logout(): Promise<void> {
  const res = await fetch(`${BASE_URL}/auth/logout`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`Logout failed: ${res.statusText}`)
}

/** Fired after the user's BYO key is saved or removed: tier-dependent views
 *  (the usage widget's model chip and cap) refetch on it. Same one-emitter,
 *  one-listener pattern as AUTH_EXPIRED_EVENT, instead of plumbing key state
 *  across sibling panels. */
export const KEY_CHANGED_EVENT = 'journal:key-changed'

/** Whether the logged-in user has stored their own Anthropic key (GET /keys).
 *  Returns only a boolean — the endpoint never returns key material. */
export async function fetchKeyStatus(): Promise<{ has_key: boolean; provider: string }> {
  const res = await fetch(`${BASE_URL}/keys`, { credentials: 'include' })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Key status failed: ${res.statusText}`)
  return res.json() as Promise<{ has_key: boolean; provider: string }>
}

/** Validate a BYO key against Anthropic and store it encrypted (POST /keys).
 *  On rejection the backend's message is surfaced (e.g. "Anthropic rejected
 *  that API key") so the form can show why. */
export async function saveApiKey(apiKey: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/keys`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
  reportIfAuthExpired(res)
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = ((await res.json()) as { detail?: string }).detail
    } catch {
      /* no JSON body */
    }
    throw new Error(detail ?? `Saving the key failed: ${res.statusText}`)
  }
  window.dispatchEvent(new Event(KEY_CHANGED_EVENT))
}

/** Remove the stored key, reverting the user to the free tier (DELETE /keys). */
export async function deleteApiKey(): Promise<void> {
  const res = await fetch(`${BASE_URL}/keys`, { method: 'DELETE', credentials: 'include' })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Removing the key failed: ${res.statusText}`)
  window.dispatchEvent(new Event(KEY_CHANGED_EVENT))
}

/** One UTC day of LLM spend in the usage window. */
export interface UsageDay {
  date: string
  spent_usd: number
  calls: number
}

/** The logged-in user's LLM spend (GET /usage). budget_usd/remaining_usd are
 *  null when the user runs on their own key — uncapped, not "$0 left". */
export interface UsageSummary {
  on_own_key: boolean
  model: string
  spent_today_usd: number
  calls_today: number
  budget_usd: number | null
  remaining_usd: number | null
  daily: UsageDay[]
}

export async function fetchUsage(days = 30): Promise<UsageSummary> {
  const res = await fetch(`${BASE_URL}/usage?days=${days}`, { credentials: 'include' })
  reportIfAuthExpired(res)
  if (!res.ok) throw new Error(`Usage failed: ${res.statusText}`)
  return res.json() as Promise<UsageSummary>
}

export function exportUrl(format: 'csv' | 'json', source: string | null = null): string {
  // source=null → all sources. Used as an <a href download> target — a top-level
  // navigation, so the SameSite=Lax session cookie is sent without any fetch options.
  const params = new URLSearchParams({ format })
  if (source) params.set('source', source)
  return `${BASE_URL}/export?${params}`
}
