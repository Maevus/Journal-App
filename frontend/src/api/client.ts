const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

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
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ entries, source }),
  })
  if (!res.ok) throw new Error(`Ingest failed: ${res.statusText}`)
  return res.json() as Promise<{ ingested: number; skipped: number }>
}

export async function searchEntries(
  query: string,
  limit = 10,
): Promise<{ results: SearchResult[] }> {
  const res = await fetch(`${BASE_URL}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, limit }),
  })
  if (!res.ok) throw new Error(`Search failed: ${res.statusText}`)
  return res.json() as Promise<{ results: SearchResult[] }>
}

export async function fetchTrends(days = 30, source: string | null = null): Promise<TrendPoint[]> {
  // days=0 → all time; source=null → all sources
  const params = new URLSearchParams({ days: String(days) })
  if (source) params.set('source', source)
  const res = await fetch(`${BASE_URL}/trends?${params}`)
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
  const res = await fetch(`${BASE_URL}/entries?${params}`)
  if (!res.ok) throw new Error(`Entries failed: ${res.statusText}`)
  const data = (await res.json()) as { entries: DayEntry[] }
  return data.entries
}

export function synthesisUrl(limit = 20, days = 0, source: string | null = null): string {
  // days=0 → all time; source=null → all sources
  const params = new URLSearchParams({ limit: String(limit), days: String(days) })
  if (source) params.set('source', source)
  return `${BASE_URL}/stream/synthesis?${params}`
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

/** The logged-in identity, from GET /auth/me. */
export interface AuthUser {
  id: number
  email: string
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

export function exportUrl(format: 'csv' | 'json', source: string | null = null): string {
  // source=null → all sources. Used as an <a href download> target.
  const params = new URLSearchParams({ format })
  if (source) params.set('source', source)
  return `${BASE_URL}/export?${params}`
}
