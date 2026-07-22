import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AuthWidget } from './AuthWidget'

// Purely presentational since the login gate took over the logged-out state:
// App owns useAuth and only renders the widget for a signed-in user.
describe('AuthWidget', () => {
  it('shows the email and reports sign-out clicks', async () => {
    const onSignOut = vi.fn()
    render(<AuthWidget user={{ id: 1, email: 'a@example.com', demo: false }} onSignOut={onSignOut} />)

    screen.getByText('a@example.com')
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }))

    expect(onSignOut).toHaveBeenCalledTimes(1)
  })

  it('shows a Demo badge and no sign-out in demo mode', () => {
    // Demo is a shared showcase account — sign-out would drop onto the login
    // gate, whose Google login can only error or be ignored.
    render(
      <AuthWidget user={{ id: 1, email: 'demo@journal.local', demo: true }} onSignOut={vi.fn()} />,
    )
    expect(screen.getByText(/demo mode/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sign out/i })).not.toBeInTheDocument()
  })
})
