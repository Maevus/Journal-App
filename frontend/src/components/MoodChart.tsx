import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useState } from 'react'
import { useTrends } from '../hooks/useTrends'
import { DayDetail } from './DayDetail'
import { SkeletonLoader } from './SkeletonLoader'
import type { SourceFilter } from './SourceSelector'
import type { TimeRange } from './TimeRangeSelector'

// ISO "YYYY-MM-DD" → euro-friendly forms
const ddmm = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}`
const mmyy = (iso: string) => `${iso.slice(5, 7)}-${iso.slice(2, 4)}`
const ddmmyyyy = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`

interface MoodChartProps {
  range: TimeRange
  source: SourceFilter
}

export function MoodChart({ range, source }: MoodChartProps) {
  const { data, loading, error } = useTrends(range.days, source.value)
  // ISO "YYYY-MM-DD" of the day being drilled into, via chart click or date picker
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  // dd-mm ticks get ambiguous once the window spans years; fall back to mm-yy
  // (still day-before-month ordering in the tooltip, which shows the full date).
  const tickFormat = range.days > 0 && range.days <= 180 ? ddmm : mmyy

  if (loading) return <SkeletonLoader lines={6} />
  if (error) return <p className="text-sm text-red-500">Failed to load trends: {error}</p>

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="font-display text-xl font-bold lowercase">
          {range.label.toLowerCase()}
          {source.value && ` · ${source.label.toLowerCase()}`}
        </span>
        <label className="flex items-center gap-2 text-sm font-medium text-ink/60">
          View a day
          <input
            type="date"
            value={selectedDate ?? ''}
            onChange={(e) => setSelectedDate(e.target.value || null)}
            className="rounded-brutal border-2 border-ink bg-white px-2 py-1 text-sm font-medium text-ink focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink"
          />
        </label>
      </div>
      {data.length === 0 && (
        <p className="py-8 text-center text-sm font-medium text-ink/50">
          No scored entries for this source in this period.
        </p>
      )}
      {data.length > 0 && (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart
            data={data}
            margin={{ top: 4, right: 16, left: -16, bottom: 0 }}
            className="cursor-pointer"
            onClick={(state: unknown) => {
              const label = (state as { activeLabel?: string } | null)?.activeLabel
              if (label) setSelectedDate(label)
            }}
          >
            {/* Hexes mirror the @theme tokens in index.css (recharts can't read CSS vars) */}
            <CartesianGrid strokeDasharray="4 4" stroke="#e2d8c0" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: '#211e1a', opacity: 0.5 }}
              stroke="#211e1a"
              tickFormatter={tickFormat}
            />
            <YAxis
              domain={[0, 1]}
              tick={{ fontSize: 11, fill: '#211e1a', opacity: 0.5 }}
              stroke="#211e1a"
              tickFormatter={(v: number) => v.toFixed(1)}
            />
            <Tooltip
              formatter={(v: number) => [v.toFixed(3), 'Mood score']}
              labelFormatter={(label: string) => ddmmyyyy(label)}
              labelStyle={{ fontSize: 12, fontWeight: 700, color: '#211e1a' }}
              contentStyle={{
                border: '2px solid #211e1a',
                borderRadius: 2,
                boxShadow: '3px 3px 0 0 #211e1a',
                fontSize: 12,
              }}
            />
            <Line
              type="monotone"
              dataKey="score"
              stroke="#f4699b"
              strokeWidth={3}
              dot={false}
              isAnimationActive={false}
              activeDot={{ r: 5, fill: '#8a74e8', stroke: '#211e1a', strokeWidth: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}

      {selectedDate && (
        <DayDetail
          date={selectedDate}
          source={source}
          onClose={() => setSelectedDate(null)}
        />
      )}
    </div>
  )
}
