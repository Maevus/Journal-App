import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '../api/client'
import { DeleteAccountPanel } from './DeleteAccountPanel'

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return { ...actual, deleteAccount: vi.fn() }
})

const deleteButton = () => screen.getByRole('button', { name: /delete my account/i })

function type(value: string) {
  fireEvent.change(screen.getByLabelText(/type delete to confirm/i), { target: { value } })
}

describe('DeleteAccountPanel', () => {
  beforeEach(() => vi.clearAllMocks())

  it('replaces the control with a note in demo mode', () => {
    render(<DeleteAccountPanel demo />)
    expect(screen.getByText(/shared account/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete my account/i })).not.toBeInTheDocument()
  })

  it('keeps the button disabled until the confirmation word is typed exactly', () => {
    render(<DeleteAccountPanel />)
    expect(deleteButton()).toBeDisabled()

    type('delete') // right word, wrong case
    expect(deleteButton()).toBeDisabled()

    type('DELETE ME')
    expect(deleteButton()).toBeDisabled()

    type('DELETE')
    expect(deleteButton()).toBeEnabled()
  })

  it('does not call the API while the confirmation is incomplete', () => {
    render(<DeleteAccountPanel />)
    type('DELET')
    fireEvent.keyDown(screen.getByLabelText(/type delete to confirm/i), { key: 'Enter' })
    expect(client.deleteAccount).not.toHaveBeenCalled()
  })

  it('deletes the account and reports what went with it', async () => {
    vi.mocked(client.deleteAccount).mockResolvedValue({ journalentry: 12, users: 1 })
    render(<DeleteAccountPanel />)

    type('DELETE')
    fireEvent.click(deleteButton())

    await waitFor(() => expect(client.deleteAccount).toHaveBeenCalledOnce())
    expect(await screen.findByText(/12 journal entries/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete my account/i })).not.toBeInTheDocument()
  })

  it('singularises the count for a one-entry journal', async () => {
    vi.mocked(client.deleteAccount).mockResolvedValue({ journalentry: 1, users: 1 })
    render(<DeleteAccountPanel />)

    type('DELETE')
    fireEvent.click(deleteButton())

    expect(await screen.findByText(/1 journal entry/)).toBeInTheDocument()
  })

  it('returning to sign-in flips the app to logged-out', async () => {
    vi.mocked(client.deleteAccount).mockResolvedValue({ journalentry: 3, users: 1 })
    const expired = vi.fn()
    window.addEventListener(client.AUTH_EXPIRED_EVENT, expired)
    render(<DeleteAccountPanel />)

    type('DELETE')
    fireEvent.click(deleteButton())
    fireEvent.click(await screen.findByRole('button', { name: /return to sign-in/i }))

    expect(expired).toHaveBeenCalledOnce()
    window.removeEventListener(client.AUTH_EXPIRED_EVENT, expired)
  })

  it('surfaces a refusal and leaves the form usable', async () => {
    vi.mocked(client.deleteAccount).mockRejectedValue(
      new Error('The shared demo account cannot be deleted'),
    )
    render(<DeleteAccountPanel />)

    type('DELETE')
    fireEvent.click(deleteButton())

    expect(await screen.findByText(/shared demo account/)).toBeInTheDocument()
    expect(deleteButton()).toBeEnabled() // still on the form, retryable
  })
})
