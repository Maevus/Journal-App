export interface TimeRange {
  label: string
  days: number // 0 = all time
}

export const TIME_RANGES: TimeRange[] = [
  { label: '30 days', days: 30 },
  { label: '3 months', days: 90 },
  { label: '6 months', days: 180 },
  { label: 'Year', days: 365 },
  { label: 'All time', days: 0 },
]

interface TimeRangeSelectorProps {
  value: TimeRange
  onChange: (range: TimeRange) => void
}

export function TimeRangeSelector({ value, onChange }: TimeRangeSelectorProps) {
  return (
    <div role="group" aria-label="Time period" className="inline-flex gap-2">
      {TIME_RANGES.map((range) => (
        <button
          key={range.label}
          onClick={() => onChange(range)}
          aria-pressed={range.days === value.days}
          className={`btn-brutal px-3 py-1 text-sm ${
            range.days === value.days
              ? 'bg-rose text-ink'
              : 'bg-white text-ink/60 hover:text-ink'
          }`}
        >
          {range.label}
        </button>
      ))}
    </div>
  )
}
