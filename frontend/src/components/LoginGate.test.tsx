import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { LoginGate } from './LoginGate'

vi.mock('../api/client', () => ({
  loginUrl: () => 'http://localhost:8000/auth/login',
}))

describe('LoginGate', () => {
  it('offers Sign in with Google as the only way forward', () => {
    render(<LoginGate />)

    const link = screen.getByRole('link', { name: /sign in with google/i })
    expect(link).toHaveAttribute('href', 'http://localhost:8000/auth/login')
  })
})
