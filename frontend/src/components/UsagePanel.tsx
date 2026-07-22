import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useUsage } from '../hooks/useUsage'
import { SkeletonLoader } from './SkeletonLoader'

// ISO "YYYY-MM-DD" → euro-friendly forms (same locals as MoodChart)
const ddmm = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}`
const ddmmyyyy = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`

/** "claude-opus-4-8" → "Opus" — the tier chip wants the family, not the id. */
const modelFamily = (model: string) => {
  const family = model.replace(/^claude-/, '').split('-')[0]
  return family.charAt(0).toUpperCase() + family.slice(1)
}

/** Cents precision for readable amounts; sub-cent spend gets 4 decimals so a
 *  $0.008 day doesn't render as a confusing "$0.01". */
const usd = (v: number) => `$${v.toFixed(v > 0 && v < 0.01 ? 4 : 2)}`

/** Per-user LLM spend (F5): today's numbers, the tier cap (or "own key, no
 *  cap"), and a 30-day spend chart. Every number is the quota gate's own view
 *  (GET /usage reads the same meter the gate enforces). */
export function UsagePanel() {
  const { data, loading, error } = useUsage()

  if (loading) return <SkeletonLoader lines={4} />
  if (error) return <p className="text-sm font-bold text-berry">Failed to load usage: {error}</p>
  if (!data) return null

  const anySpend = data.daily.some((d) => d.calls > 0)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <span
          className={`rounded-brutal border-2 border-ink px-2 py-0.5 text-xs font-bold uppercase tracking-wide ${
            data.on_own_key ? 'bg-mint' : 'bg-paper'
          }`}
        >
          {data.on_own_key ? 'Your key' : 'Free tier'} · {modelFamily(data.model)}
        </span>
        <p className="text-sm font-medium text-ink/70">
          Today: <span className="font-bold text-ink">{usd(data.spent_today_usd)}</span> ·{' '}
          {data.calls_today} {data.calls_today === 1 ? 'call' : 'calls'}
        </p>
      </div>

      <p className="text-sm font-medium text-ink/70">
        {data.remaining_usd === null || data.budget_usd === null ? (
          'Spending on your own Anthropic key — no daily cap.'
        ) : (
          <>
            Remaining today:{' '}
            <span className="font-bold text-ink">{usd(data.remaining_usd)}</span> of{' '}
            {usd(data.budget_usd)}
          </>
        )}
      </p>

      {anySpend ? (
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={data.daily} margin={{ top: 4, right: 16, left: -8, bottom: 0 }}>
            {/* Hexes mirror the @theme tokens in index.css (recharts can't read CSS vars) */}
            <CartesianGrid strokeDasharray="4 4" stroke="#e2d8c0" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 11, fill: '#211e1a', opacity: 0.5 }}
              stroke="#211e1a"
              tickFormatter={ddmm}
            />
            <YAxis
              tick={{ fontSize: 11, fill: '#211e1a', opacity: 0.5 }}
              stroke="#211e1a"
              width={48}
              tickFormatter={(v: number) => `$${parseFloat(v.toFixed(4))}`}
            />
            <Tooltip
              formatter={(v: number) => [`$${v.toFixed(4)}`, 'Spend']}
              labelFormatter={(label: string) => ddmmyyyy(label)}
              labelStyle={{ fontSize: 12, fontWeight: 700, color: '#211e1a' }}
              contentStyle={{
                border: '2px solid #211e1a',
                borderRadius: 2,
                boxShadow: '3px 3px 0 0 #211e1a',
                fontSize: 12,
              }}
            />
            <Bar
              dataKey="spent_usd"
              fill="#e8a81c"
              stroke="#211e1a"
              strokeWidth={1}
              isAnimationActive={false}
            />
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <p className="py-4 text-center text-sm font-medium text-ink/50">
          No LLM calls in the last 30 days.
        </p>
      )}
    </div>
  )
}
