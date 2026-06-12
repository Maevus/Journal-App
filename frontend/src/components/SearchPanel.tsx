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
      <h2 className="mb-4 text-xl font-semibold text-gray-900">Semantic Search</h2>

      <div className="mb-4 flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void handleSearch()}
          placeholder="Search your journal…"
          className="flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
        <button
          onClick={() => void handleSearch()}
          disabled={loading}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      {error && <p className="mb-2 text-sm text-red-500">{error}</p>}

      {loading && <SkeletonLoader lines={3} />}

      {!loading && (
        <div className="flex-1 space-y-3 overflow-y-auto">
          {results.map((result) => (
            <div key={result.id} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <div className="mb-1 flex items-center gap-3 text-xs text-gray-400">
                <span className="font-medium uppercase tracking-wide">{result.source}</span>
                <span>{new Date(result.timestamp).toLocaleDateString()}</span>
                <span>score {result.similarity_score.toFixed(3)}</span>
                {sourceNoteUrl(result.source, result.external_id) && (
                  <a
                    href={sourceNoteUrl(result.source, result.external_id)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto font-medium text-indigo-500 hover:text-indigo-700 hover:underline"
                  >
                    Open in Keep ↗
                  </a>
                )}
              </div>
              <p className="line-clamp-3 text-sm text-gray-700">{result.content}</p>
            </div>
          ))}
          {searched && results.length === 0 && !loading && (
            <p className="text-sm text-gray-400">No results found.</p>
          )}
        </div>
      )}
    </div>
  )
}
