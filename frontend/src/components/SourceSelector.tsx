export interface SourceFilter {
  label: string
  value: string | null // JournalEntry.source value; null = all sources
}

export const SOURCE_FILTERS: SourceFilter[] = [
  { label: 'All sources', value: null },
  { label: 'Keep', value: 'google_keep' },
  { label: 'GoodNotes', value: 'goodnotes' },
]

interface SourceSelectorProps {
  value: SourceFilter
  onChange: (source: SourceFilter) => void
}

export function SourceSelector({ value, onChange }: SourceSelectorProps) {
  return (
    <div role="group" aria-label="Journal source" className="inline-flex gap-2">
      {SOURCE_FILTERS.map((sourceFilter) => (
        <button
          key={sourceFilter.label}
          onClick={() => onChange(sourceFilter)}
          aria-pressed={sourceFilter.value === value.value}
          className={`btn-brutal px-3 py-1 text-sm ${
            sourceFilter.value === value.value
              ? 'bg-gold text-ink'
              : 'bg-white text-ink/60 hover:text-ink'
          }`}
        >
          {sourceFilter.label}
        </button>
      ))}
    </div>
  )
}
