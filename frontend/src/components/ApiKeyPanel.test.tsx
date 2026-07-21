import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import * as client from '../api/client'
import { ApiKeyPanel } from './ApiKeyPanel'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    fetchKeyStatus: vi.fn(),
    saveApiKey: vi.fn(),
    deleteApiKey: vi.fn(),
  }
})

describe('ApiKeyPanel', () => {
  it('shows the add-key form when the user has no key', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: false, provider: 'anthropic' })
    render(<ApiKeyPanel />)
    expect(await screen.findByPlaceholderText(/sk-ant/)).toBeInTheDocument()
  })

  it('shows the your-key state (and a remove button) when a key is set', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: true, provider: 'anthropic' })
    render(<ApiKeyPanel />)
    expect(await screen.findByRole('button', { name: /remove key/i })).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/sk-ant/)).not.toBeInTheDocument()
  })

  it('validates and saves a key, then switches to the your-key state', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: false, provider: 'anthropic' })
    vi.mocked(client.saveApiKey).mockResolvedValue(undefined)
    render(<ApiKeyPanel />)

    fireEvent.change(await screen.findByPlaceholderText(/sk-ant/), {
      target: { value: 'sk-ant-good' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save key/i }))

    await waitFor(() => expect(client.saveApiKey).toHaveBeenCalledWith('sk-ant-good'))
    expect(await screen.findByRole('button', { name: /remove key/i })).toBeInTheDocument()
  })

  it('surfaces a rejected key and stays on the form', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: false, provider: 'anthropic' })
    vi.mocked(client.saveApiKey).mockRejectedValue(new Error('Anthropic rejected that API key'))
    render(<ApiKeyPanel />)

    fireEvent.change(await screen.findByPlaceholderText(/sk-ant/), {
      target: { value: 'sk-ant-bad' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save key/i }))

    expect(await screen.findByText(/Anthropic rejected/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/sk-ant/)).toBeInTheDocument() // still on the form
  })

  it('removes a key and returns to the add-key form', async () => {
    vi.mocked(client.fetchKeyStatus).mockResolvedValue({ has_key: true, provider: 'anthropic' })
    vi.mocked(client.deleteApiKey).mockResolvedValue(undefined)
    render(<ApiKeyPanel />)

    fireEvent.click(await screen.findByRole('button', { name: /remove key/i }))
    expect(await screen.findByPlaceholderText(/sk-ant/)).toBeInTheDocument()
  })
})
