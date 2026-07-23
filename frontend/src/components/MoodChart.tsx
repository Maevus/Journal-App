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

const dateInputClass =
  'rounded-brutal border-2 border-ink bg-white px-2 py-1 text-sm font-medium text-ink focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink'

interface MoodChartProps {
  range: TimeRange
  source: SourceFilter
}

export function MoodChart({ range, source }: MoodChartProps) {
  // A custom [from, to] window (both set) overrides the preset `range`
  // server-side. Picking a preset clears it — App remounts this component with
  // key={range.days}, resetting the window without a set-state-in-effect.
  const [customStart, setCustomStart] = useState<string | null>(null)
  const [customEnd, setCustomEnd] = useState<string | null>(null)

  const activeWindow = Boolean(customStart && customEnd)
  const { data, loading, error } = useTrends(
    range.days,
    source.value,
    activeWindow ? customStart : null,
    activeWindow ? customEnd : null,
  )
  // ISO "YYYY-MM-DD" of the day being drilled into, via chart click or date picker
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  // dd-mm ticks get ambiguous once the window spans years; fall back to mm-yy.
  // For a custom window, judge the span from its endpoints.
  const spanDays = activeWindow
    ? (Date.parse(customEnd!) - Date.parse(customStart!)) / 86_400_000
    : range.days
  const tickFormat = spanDays > 0 && spanDays <= 180 ? ddmm : mmyy

  if (loading) return <SkeletonLoader lines={6} />
  if (error) return <p className="text-sm text-red-500">Failed to load trends: {error}</p>

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <span className="font-display text-xl font-bold lowercase">
          {activeWindow ? `${customStart} → ${customEnd}` : range.label.toLowerCase()}
          {source.value && ` · ${source.label.toLowerCase()}`}
        </span>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm font-medium text-ink/60">
            From
            <input
              type="date"
              aria-label="Range start"
              value={customStart ?? ''}
              max={customEnd ?? undefined}
              onChange={(e) => setCustomStart(e.target.value || null)}
              className={dateInputClass}
            />
          </label>
          <label className="flex items-center gap-2 text-sm font-medium text-ink/60">
            To
            <input
              type="date"
              aria-label="Range end"
              value={customEnd ?? ''}
              min={customStart ?? undefined}
              onChange={(e) => setCustomEnd(e.target.value || null)}
              className={dateInputClass}
            />
          </label>
          {(customStart || customEnd) && (
            <button
              onClick={() => {
                setCustomStart(null)
                setCustomEnd(null)
              }}
              className="btn-brutal bg-white px-2 py-0.5 text-xs"
            >
              Clear
            </button>
          )}
          <label className="flex items-center gap-2 text-sm font-medium text-ink/60">
            View a day
            <input
              type="date"
              value={selectedDate ?? ''}
              onChange={(e) => setSelectedDate(e.target.value || null)}
              className={dateInputClass}
            />
          </label>
        </div>
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
