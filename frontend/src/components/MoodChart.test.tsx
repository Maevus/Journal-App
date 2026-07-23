import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '../api/client'
import { MoodChart } from './MoodChart'
import { SOURCE_FILTERS } from './SourceSelector'
import { TIME_RANGES } from './TimeRangeSelector'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, fetchTrends: vi.fn(), fetchEntriesByDate: vi.fn() }
})

const preset = TIME_RANGES[0] // 30 days
const allSources = SOURCE_FILTERS[0] // value: null

describe('MoodChart custom date range', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(client.fetchTrends).mockResolvedValue([{ date: '2025-06-01', score: 0.6 }])
  })

  it('queries the custom window only once both From and To are set', async () => {
    render(<MoodChart range={preset} source={allSources} />)
    // Initial load uses the preset — no custom window.
    await waitFor(() => expect(client.fetchTrends).toHaveBeenCalledWith(30, null, null, null))

    fireEvent.change(screen.getByLabelText('Range start'), { target: { value: '2025-01-01' } })
    // Only From set → still no custom window fetch (needs both).
    expect(client.fetchTrends).not.toHaveBeenCalledWith(30, null, '2025-01-01', null)

    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2025-12-31' } })
    await waitFor(() =>
      expect(client.fetchTrends).toHaveBeenLastCalledWith(30, null, '2025-01-01', '2025-12-31'),
    )
    // The title reflects the active window, not the preset label.
    expect(screen.getByText(/2025-01-01 → 2025-12-31/)).toBeInTheDocument()
  })

  it('reverts to the preset when the window is cleared', async () => {
    render(<MoodChart range={preset} source={allSources} />)
    await screen.findByLabelText('Range start') // wait out the initial load
    fireEvent.change(screen.getByLabelText('Range start'), { target: { value: '2025-01-01' } })
    fireEvent.change(screen.getByLabelText('Range end'), { target: { value: '2025-12-31' } })
    await waitFor(() =>
      expect(client.fetchTrends).toHaveBeenLastCalledWith(30, null, '2025-01-01', '2025-12-31'),
    )

    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    await waitFor(() => expect(client.fetchTrends).toHaveBeenLastCalledWith(30, null, null, null))
  })
})
