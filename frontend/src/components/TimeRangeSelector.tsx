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
    <div
      role="group"
      aria-label="Time period"
      className="inline-flex rounded-lg border border-gray-200 bg-gray-100 p-0.5"
    >
      {TIME_RANGES.map((range) => (
        <button
          key={range.label}
          onClick={() => onChange(range)}
          aria-pressed={range.days === value.days}
          className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-indigo-400 ${
            range.days === value.days
              ? 'bg-white text-indigo-700 shadow-sm'
              : 'text-gray-500 hover:text-gray-800'
          }`}
        >
          {range.label}
        </button>
      ))}
    </div>
  )
}
