import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { fetchTrends } from '../api/client'
import { useTrends } from './useTrends'

vi.mock('../api/client', () => ({ fetchTrends: vi.fn() }))

describe('useTrends', () => {
  it('loads trend points and clears loading', async () => {
    vi.mocked(fetchTrends).mockResolvedValue([{ date: '2026-05-10', score: 0.5 }])
    const { result } = renderHook(() => useTrends(30, null))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual([{ date: '2026-05-10', score: 0.5 }])
    expect(result.current.error).toBeNull()
    expect(fetchTrends).toHaveBeenCalledWith(30, null, null, null)
  })

  it('captures the error message on failure', async () => {
    vi.mocked(fetchTrends).mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useTrends())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe('boom')
  })
})
