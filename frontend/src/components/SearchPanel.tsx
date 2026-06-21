import { useState } from 'react'
import { searchEntries, sourceNoteUrl, type SearchResult } from '../api/client'
import { SkeletonLoader } from './SkeletonLoader'

export function SearchPanel() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  const handleSearch = async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    setSearched(true)
    try {
      const data = await searchEntries(q)
      setResults(data.results)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void handleSearch()}
          placeholder="Search your journal…"
          className="flex-1 rounded-brutal border-2 border-ink bg-white px-3 py-2 text-sm font-medium placeholder:text-ink/40 focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink"
        />
        <button
          onClick={() => void handleSearch()}
          disabled={loading}
          className="btn-brutal bg-mint px-4 py-2 text-sm text-ink disabled:opacity-50"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      {error && <p className="mb-2 text-sm font-bold text-berry">{error}</p>}

      {loading && <SkeletonLoader lines={3} />}

      {!loading && (
        <div className="flex-1 space-y-3 overflow-y-auto">
          {results.map((result) => (
            <div key={result.id} className="rounded-brutal border-2 border-ink bg-paper p-3">
              <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink/60">
                <span className="font-bold uppercase tracking-wide">{result.source}</span>
                <span className="font-medium">
                  {new Date(result.timestamp).toLocaleDateString()}
                </span>
                <span className="font-medium">score {result.similarity_score.toFixed(3)}</span>
                {sourceNoteUrl(result.source, result.external_id) && (
                  <a
                    href={sourceNoteUrl(result.source, result.external_id)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto font-bold text-lilac underline decoration-2 underline-offset-2 hover:bg-lilac hover:text-white"
                  >
                    Open in Keep ↗
                  </a>
                )}
              </div>
              <p className="line-clamp-3 text-sm text-ink">{result.content}</p>
            </div>
          ))}
          {searched && results.length === 0 && !loading && (
            <p className="text-sm font-medium text-ink/50">No results found.</p>
          )}
        </div>
      )}
    </div>
  )
}
