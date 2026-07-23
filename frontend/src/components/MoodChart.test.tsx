import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

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

// The DatePicker's month-grid labels (not exported from the component:
// react-refresh/only-export-components)
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

// Walk the DatePicker: open → zoom out to months → step back to the target
// year → pick month → pick day. Assumes "today" is jul 2026 (Date is faked).
function pickDate(trigger: string, iso: string) {
  const [year, month] = iso.split('-').map(Number)
  fireEvent.click(screen.getByRole('button', { name: trigger }))
  fireEvent.click(screen.getByRole('button', { name: 'jul 2026' }))
  for (let y = 2026; y > year; y--) {
    fireEvent.click(screen.getByRole('button', { name: 'Previous year' }))
  }
  fireEvent.click(screen.getByRole('button', { name: MONTHS[month - 1] }))
  fireEvent.click(screen.getByRole('button', { name: iso }))
}

describe('MoodChart custom date range', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Fake only Date (not timers — waitFor needs real ones) so the pickers
    // deterministically open on July 2026.
    vi.useFakeTimers({ toFake: ['Date'], now: new Date(2026, 6, 15) })
    vi.mocked(client.fetchTrends).mockResolvedValue([{ date: '2025-06-01', score: 0.6 }])
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('queries the custom window only once both From and To are set', async () => {
    render(<MoodChart range={preset} source={allSources} />)
    // Initial load uses the preset — no custom window.
    await waitFor(() => expect(client.fetchTrends).toHaveBeenCalledWith(30, null, null, null))

    pickDate('Range start', '2025-01-01')
    // Only From set → still no custom window fetch (needs both).
    expect(client.fetchTrends).not.toHaveBeenCalledWith(30, null, '2025-01-01', null)

    pickDate('Range end', '2025-12-31')
    await waitFor(() =>
      expect(client.fetchTrends).toHaveBeenLastCalledWith(30, null, '2025-01-01', '2025-12-31'),
    )
    // The title reflects the active window, not the preset label.
    expect(screen.getByText(/2025-01-01 → 2025-12-31/)).toBeInTheDocument()
  })

  it('reverts to the preset when the window is cleared', async () => {
    render(<MoodChart range={preset} source={allSources} />)
    await screen.findByRole('button', { name: 'Range start' }) // wait out the initial load
    pickDate('Range start', '2025-01-01')
    pickDate('Range end', '2025-12-31')
    await waitFor(() =>
      expect(client.fetchTrends).toHaveBeenLastCalledWith(30, null, '2025-01-01', '2025-12-31'),
    )

    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    await waitFor(() => expect(client.fetchTrends).toHaveBeenLastCalledWith(30, null, null, null))
  })
})
