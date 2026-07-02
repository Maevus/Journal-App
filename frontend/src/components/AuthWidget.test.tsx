import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { fetchMe, logout } from '../api/client'
import { AuthWidget } from './AuthWidget'

vi.mock('../api/client', () => ({
  fetchMe: vi.fn(),
  logout: vi.fn(),
  loginUrl: () => 'http://localhost:8000/auth/login',
}))

describe('AuthWidget', () => {
  it('offers Sign in with Google when logged out', async () => {
    vi.mocked(fetchMe).mockResolvedValue(null)
    render(<AuthWidget />)

    const link = await screen.findByRole('link', { name: /sign in with google/i })
    expect(link).toHaveAttribute('href', 'http://localhost:8000/auth/login')
  })

  it('shows the email and signs out on click', async () => {
    vi.mocked(fetchMe).mockResolvedValue({ id: 1, email: 'a@example.com' })
    vi.mocked(logout).mockResolvedValue(undefined)
    render(<AuthWidget />)

    await screen.findByText('a@example.com')
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }))

    expect(logout).toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByRole('link', { name: /sign in with google/i })).toBeInTheDocument(),
    )
  })
})
