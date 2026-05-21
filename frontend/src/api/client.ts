const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000'

export interface SearchResult {
  id: number
  content: string
  source: string
  timestamp: string
  similarity_score: number
}

export interface TrendPoint {
  date: string
  score: number
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
  const res = await fetch(`${BASE_URL}/trends?days=${days}`)
  if (!res.ok) throw new Error(`Trends failed: ${res.statusText}`)
  const data = (await res.json()) as { trends: TrendPoint[] }
  return data.trends
}

export function synthesisUrl(limit = 20): string {
  return `${BASE_URL}/stream/synthesis?limit=${limit}`
}
