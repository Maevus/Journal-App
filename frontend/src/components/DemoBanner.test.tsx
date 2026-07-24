import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { loginUrl } from '../api/client'
import { DemoBanner } from './DemoBanner'

describe('DemoBanner', () => {
  it('explains the read-only demo and links to Google sign-in', () => {
    render(<DemoBanner />)
    expect(screen.getByText(/exploring a sample journal/i)).toBeInTheDocument()
    expect(screen.getByText(/read-only/i)).toBeInTheDocument()

    const link = screen.getByRole('link', { name: /sign in with google/i })
    expect(link).toHaveAttribute('href', loginUrl())
  })
})
