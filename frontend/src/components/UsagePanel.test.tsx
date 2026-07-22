import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as client from '../api/client'
import type { UsageSummary } from '../api/client'
import { UsagePanel } from './UsagePanel'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    fetchUsage: vi.fn(),
  }
})

const freeTier: UsageSummary = {
  on_own_key: false,
  model: 'claude-haiku-4-5',
  spent_today_usd: 0,
  calls_today: 0,
  budget_usd: 0,
  remaining_usd: 0,
  daily: [{ date: '2026-07-22', spent_usd: 0, calls: 0 }],
}

const byoWithSpend: UsageSummary = {
  on_own_key: true,
  model: 'claude-opus-4-8',
  spent_today_usd: 0.1795,
  calls_today: 21,
  budget_usd: null,
  remaining_usd: null,
  daily: [
    { date: '2026-07-21', spent_usd: 0.05, calls: 4 },
    { date: '2026-07-22', spent_usd: 0.1795, calls: 21 },
  ],
}

describe('UsagePanel', () => {
  it('shows the free tier chip, budget, and empty-window message', async () => {
    vi.mocked(client.fetchUsage).mockResolvedValue(freeTier)
    render(<UsagePanel />)
    expect(await screen.findByText(/free tier · haiku/i)).toBeInTheDocument()
    expect(screen.getByText(/remaining today/i)).toBeInTheDocument()
    expect(screen.getByText(/no llm calls in the last 30 days/i)).toBeInTheDocument()
  })

  it('shows the own-key chip, spend, and no-cap line for a BYO user', async () => {
    vi.mocked(client.fetchUsage).mockResolvedValue(byoWithSpend)
    render(<UsagePanel />)
    expect(await screen.findByText(/your key · opus/i)).toBeInTheDocument()
    expect(screen.getByText('$0.18')).toBeInTheDocument()
    expect(screen.getByText(/21 calls/)).toBeInTheDocument()
    expect(screen.getByText(/no daily cap/i)).toBeInTheDocument()
    expect(screen.queryByText(/remaining today/i)).not.toBeInTheDocument()
  })

  it('refetches when the BYO key changes (KEY_CHANGED_EVENT)', async () => {
    vi.mocked(client.fetchUsage).mockResolvedValueOnce(freeTier)
    render(<UsagePanel />)
    expect(await screen.findByText(/free tier · haiku/i)).toBeInTheDocument()

    // The user saves a key in the sibling panel → the widget must not go stale.
    vi.mocked(client.fetchUsage).mockResolvedValueOnce(byoWithSpend)
    window.dispatchEvent(new Event(client.KEY_CHANGED_EVENT))
    expect(await screen.findByText(/your key · opus/i)).toBeInTheDocument()
  })

  it('surfaces a load failure', async () => {
    vi.mocked(client.fetchUsage).mockRejectedValue(new Error('Usage failed: boom'))
    render(<UsagePanel />)
    expect(await screen.findByText(/failed to load usage/i)).toBeInTheDocument()
  })
})
