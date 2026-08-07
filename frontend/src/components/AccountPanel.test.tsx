import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '../api/client'
import type { UsageSummary } from '../api/client'
import { AccountPanel } from './AccountPanel'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    fetchKeyStatus: vi.fn(),
    fetchUsage: vi.fn(),
    saveApiKey: vi.fn(),
    deleteApiKey: vi.fn(),
  }
})

const usage: UsageSummary = {
  on_own_key: true,
  model: 'claude-opus-4-8',
  spent_today_usd: 0.18,
  calls_today: 21,
  budget_usd: null,
  remaining_usd: null,
  daily: [{ date: '2026-07-22', spent_usd: 0.18, calls: 21 }],
}

describe('AccountPanel', () => {
  // No auto-clear is configured, so isolate call counts (the demo test asserts
  // fetchKeyStatus was never called).
  beforeEach(() => vi.clearAllMocks())

  it('opens on the API-key tab when a signed-in user has no key', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: false, provider: 'anthropic' })
    render(<AccountPanel demo={false} />)
    // Default lands on the key tab → the add-key form is visible.
    expect(await screen.findByPlaceholderText(/sk-ant/)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /api key/i })).toHaveAttribute('aria-selected', 'true')
  })

  it('opens on Usage when the signed-in user already has a key', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: true, provider: 'anthropic' })
    vi.mocked(client.fetchUsage).mockResolvedValue(usage)
    render(<AccountPanel demo={false} />)
    expect(await screen.findByText(/your key · opus/i)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /usage/i })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByPlaceholderText(/sk-ant/)).not.toBeInTheDocument()
  })

  it('opens on Usage in demo mode without probing key status', async () => {
    vi.mocked(client.fetchUsage).mockResolvedValue(usage)
    render(<AccountPanel demo />)
    expect(await screen.findByText(/your key · opus/i)).toBeInTheDocument()
    expect(client.fetchKeyStatus).not.toHaveBeenCalled()
  })

  it('switches tabs when the sticker labels are clicked', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: true, provider: 'anthropic' })
    vi.mocked(client.fetchUsage).mockResolvedValue(usage)
    render(<AccountPanel demo={false} />)
    await screen.findByText(/your key · opus/i) // starts on Usage

    fireEvent.click(screen.getByRole('tab', { name: /api key/i }))
    expect(await screen.findByRole('button', { name: /remove key/i })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /usage/i }))
    expect(await screen.findByText(/your key · opus/i)).toBeInTheDocument()
  })

  it('reaches account deletion from its own tab, and never opens on it', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: false, provider: 'anthropic' })
    render(<AccountPanel demo={false} />)
    // The no-key default is the API-key tab, not the destructive one.
    await screen.findByPlaceholderText(/sk-ant/)
    const deleteTab = screen.getByRole('tab', { name: /delete account/i })
    expect(deleteTab).toHaveAttribute('aria-selected', 'false')

    fireEvent.click(deleteTab)

    expect(await screen.findByRole('button', { name: /delete my account/i })).toBeDisabled()
  })
})
