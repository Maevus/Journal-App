import { useEffect, useState } from 'react'
import { fetchEntriesByDate, sourceNoteUrl, type DayEntry } from '../api/client'
import { SkeletonLoader } from './SkeletonLoader'

const ddmmyyyy = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`

function moodColor(score: number): string {
  if (score >= 0.66) return 'bg-green-100 text-green-700'
  if (score >= 0.33) return 'bg-amber-100 text-amber-700'
  return 'bg-red-100 text-red-700'
}

interface DayDetailProps {
  date: string // ISO "YYYY-MM-DD"
  onClose: () => void
}

export function DayDetail({ date, onClose }: DayDetailProps) {
  const [entries, setEntries] = useState<DayEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchEntriesByDate(date)
      .then((result) => {
        if (!cancelled) setEntries(result)
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [date])

  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Entries on {ddmmyyyy(date)}</h3>
        <button
          onClick={onClose}
          aria-label="Close day detail"
          className="rounded px-2 py-0.5 text-sm text-gray-400 hover:bg-gray-200 hover:text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          ✕
        </button>
      </div>

      {loading && <SkeletonLoader lines={3} />}
      {error && <p className="text-sm text-red-500">Failed to load entries: {error}</p>}

      {!loading && !error && entries.length === 0 && (
        <p className="text-sm text-gray-400">No entries on this day.</p>
      )}

      {!loading && !error && entries.length > 0 && (
        <ul className="max-h-80 space-y-3 overflow-y-auto">
          {entries.map((entry) => (
            <li key={entry.id} className="rounded-md border border-gray-200 bg-white p-3">
              <div className="mb-1.5 flex items-center gap-2 text-xs">
                <span className="rounded bg-gray-100 px-1.5 py-0.5 font-medium text-gray-500">
                  {entry.source}
                </span>
                {entry.mood_score !== null && (
                  <span
                    className={`rounded px-1.5 py-0.5 font-medium ${moodColor(entry.mood_score)}`}
                  >
                    mood {entry.mood_score.toFixed(2)}
                  </span>
                )}
                {sourceNoteUrl(entry.source, entry.external_id) && (
                  <a
                    href={sourceNoteUrl(entry.source, entry.external_id)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto font-medium text-indigo-500 hover:text-indigo-700 hover:underline"
                  >
                    Open in Keep ↗
                  </a>
                )}
              </div>
              <p className="whitespace-pre-wrap text-sm text-gray-800">{entry.content}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
