import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as client from '../api/client'
import { DayDetail } from './DayDetail'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, fetchEntriesByDate: vi.fn() } // keep real sourceNoteUrl
})

const source = { value: null, label: 'All' }

const entry = {
  id: 1,
  content: 'Climbed a V3',
  source: 'google_keep',
  timestamp: '2026-05-10T09:00:00',
  imported_at: null,
  mood_score: 0.91,
  mood_reasoning: 'energised and accomplished',
  external_id: 'k1',
}

describe('DayDetail', () => {
  it('renders an entry with its mood score and reasoning', async () => {
    vi.mocked(client.fetchEntriesByDate).mockResolvedValue([entry])
    render(<DayDetail date="2026-05-10" source={source} onClose={() => {}} />)

    expect(await screen.findByText('Climbed a V3')).toBeInTheDocument()
    expect(screen.getByText(/energised and accomplished/)).toBeInTheDocument()
    expect(screen.getByText(/mood 0\.91/)).toBeInTheDocument()
  })

  it('shows the empty message when there are no entries', async () => {
    vi.mocked(client.fetchEntriesByDate).mockResolvedValue([])
    render(<DayDetail date="2026-05-10" source={source} onClose={() => {}} />)
    expect(await screen.findByText(/No entries on this day/)).toBeInTheDocument()
  })

  it('shows an error message when the fetch fails', async () => {
    vi.mocked(client.fetchEntriesByDate).mockRejectedValue(new Error('network down'))
    render(<DayDetail date="2026-05-10" source={source} onClose={() => {}} />)
    expect(await screen.findByText(/Failed to load entries/)).toBeInTheDocument()
  })
})
