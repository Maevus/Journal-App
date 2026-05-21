import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useTrends } from '../hooks/useTrends'
import { SkeletonLoader } from './SkeletonLoader'

export function MoodChart() {
  const { data, loading, error } = useTrends(30)

  if (loading) return <SkeletonLoader lines={6} />
  if (error) return <p className="text-sm text-red-500">Failed to load trends: {error}</p>

  return (
    <div>
      <h2 className="mb-4 text-xl font-semibold text-gray-900">Mood Trends — last 30 days</h2>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data} margin={{ top: 4, right: 16, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={(v: string) => v.slice(5)} // "MM-DD"
          />
          <YAxis
            domain={[0, 1]}
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            tickFormatter={(v: number) => v.toFixed(1)}
          />
          <Tooltip
            formatter={(v: number) => [v.toFixed(3), 'Mood score']}
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
    </div>
  )
}
