import { useEffect, useState } from 'react'
import { fetchTrends, type TrendPoint } from '../api/client'

export function useTrends(days = 30) {
  const [data, setData] = useState<TrendPoint[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetchTrends(days)
      .then((points) => {
        if (!cancelled) setData(points)
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
  }, [days])

  return { data, loading, error }
}
