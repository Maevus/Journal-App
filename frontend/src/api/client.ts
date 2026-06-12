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

export async function fetchTrends(days = 30): Promise<TrendPoint[]> {
  // days=0 → all time
  const res = await fetch(`${BASE_URL}/trends?days=${days}`)
  if (!res.ok) throw new Error(`Trends failed: ${res.statusText}`)
  const data = (await res.json()) as { trends: TrendPoint[] }
  return data.trends
}

export async function fetchEntriesByDate(date: string): Promise<DayEntry[]> {
  // date: ISO "YYYY-MM-DD"
  const res = await fetch(`${BASE_URL}/entries?date=${date}`)
  if (!res.ok) throw new Error(`Entries failed: ${res.statusText}`)
  const data = (await res.json()) as { entries: DayEntry[] }
  return data.entries
}

export function synthesisUrl(limit = 20, days = 0): string {
  // days=0 → all time
  return `${BASE_URL}/stream/synthesis?limit=${limit}&days=${days}`
}
