import { useEffect, useState } from 'react'
import { fetchEntriesByDate, sourceNoteUrl, type DayEntry } from '../api/client'
import { SkeletonLoader } from './SkeletonLoader'
import type { SourceFilter } from './SourceSelector'

const ddmmyyyy = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`

function moodColor(score: number): string {
  if (score >= 0.66) return 'bg-mint text-ink'
  if (score >= 0.33) return 'bg-gold text-ink'
  return 'bg-berry text-white'
}

interface DayDetailProps {
  date: string // ISO "YYYY-MM-DD"
  source: SourceFilter
  onClose: () => void
}

export function DayDetail({ date, source, onClose }: DayDetailProps) {
  const [entries, setEntries] = useState<DayEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchEntriesByDate(date, source.value)
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
  }, [date, source.value])

  return (
    <div className="mt-6 rounded-brutal border-2 border-ink bg-paper p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-display text-sm font-bold uppercase tracking-wide">
          Entries on {ddmmyyyy(date)}
        </h3>
        <button
          onClick={onClose}
          aria-label="Close day detail"
          className="btn-brutal bg-white px-2 py-0.5 text-sm"
        >
          ✕
        </button>
      </div>

      {loading && <SkeletonLoader lines={3} />}
      {error && <p className="text-sm font-bold text-berry">Failed to load entries: {error}</p>}

      {!loading && !error && entries.length === 0 && (
        <p className="text-sm font-medium text-ink/50">No entries on this day.</p>
      )}

      {!loading && !error && entries.length > 0 && (
        <ul className="max-h-80 space-y-3 overflow-y-auto">
          {entries.map((entry) => (
            <li key={entry.id} className="rounded-brutal border-2 border-ink bg-white p-3">
              <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                <span className="rounded-brutal border-2 border-ink bg-paper px-1.5 py-0.5 font-bold uppercase tracking-wide">
                  {entry.source}
                </span>
                {entry.mood_score !== null && (
                  <span
                    className={`rounded-brutal border-2 border-ink px-1.5 py-0.5 font-bold ${moodColor(entry.mood_score)}`}
                  >
                    mood {entry.mood_score.toFixed(2)}
                  </span>
                )}
                {sourceNoteUrl(entry.source, entry.external_id) && (
                  <a
                    href={sourceNoteUrl(entry.source, entry.external_id)!}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ml-auto font-bold text-lilac underline decoration-2 underline-offset-2 hover:bg-lilac hover:text-white"
                  >
                    Open in Keep ↗
                  </a>
                )}
              </div>
              <p className="whitespace-pre-wrap text-sm text-ink">{entry.content}</p>
              {entry.mood_reasoning && (
                <p className="mt-2 border-t-2 border-dashed border-ink/20 pt-2 text-xs italic text-ink/60">
                  Mood read: {entry.mood_reasoning}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
