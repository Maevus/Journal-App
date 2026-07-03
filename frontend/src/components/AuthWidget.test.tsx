import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AuthWidget } from './AuthWidget'

// Purely presentational since the login gate took over the logged-out state:
// App owns useAuth and only renders the widget for a signed-in user.
describe('AuthWidget', () => {
  it('shows the email and reports sign-out clicks', async () => {
    const onSignOut = vi.fn()
    render(<AuthWidget user={{ id: 1, email: 'a@example.com' }} onSignOut={onSignOut} />)

    screen.getByText('a@example.com')
    await userEvent.click(screen.getByRole('button', { name: /sign out/i }))

    expect(onSignOut).toHaveBeenCalledTimes(1)
  })
})
