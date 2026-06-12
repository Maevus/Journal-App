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
import type { TimeRange } from './TimeRangeSelector'

// ISO "YYYY-MM-DD" → euro-friendly forms
const ddmm = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}`
const mmyy = (iso: string) => `${iso.slice(5, 7)}-${iso.slice(2, 4)}`
const ddmmyyyy = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`

interface MoodChartProps {
  range: TimeRange
}

export function MoodChart({ range }: MoodChartProps) {
  const { data, loading, error } = useTrends(range.days)
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
        <h2 className="text-xl font-semibold text-gray-900">
          Mood Trends — {range.label.toLowerCase()}
        </h2>
        <label className="flex items-center gap-2 text-sm text-gray-500">
          View a day
          <input
            type="date"
            value={selectedDate ?? ''}
            onChange={(e) => setSelectedDate(e.target.value || null)}
            className="rounded-md border border-gray-200 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          />
        </label>
      </div>
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
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={tickFormat}
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={(v: number) => v.toFixed(1)}
          />
          <Tooltip
            formatter={(v: number) => [v.toFixed(3), 'Mood score']}
            labelFormatter={(label: string) => ddmmyyyy(label)}
            labelStyle={{ fontSize: 12 }}
          />
          <Line
            type="monotone"
            dataKey="score"
            stroke="#6366f1"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
          />
        </LineChart>
      </ResponsiveContainer>

      {selectedDate && <DayDetail date={selectedDate} onClose={() => setSelectedDate(null)} />}
    </div>
  )
}
