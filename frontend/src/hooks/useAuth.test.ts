import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { AUTH_EXPIRED_EVENT, fetchMe, logout } from '../api/client'
import { useAuth } from './useAuth'

vi.mock('../api/client', () => ({
  AUTH_EXPIRED_EVENT: 'journal:auth-expired',
  fetchMe: vi.fn(),
  logout: vi.fn(),
}))

describe('useAuth', () => {
  it('resolves to the logged-in user', async () => {
    vi.mocked(fetchMe).mockResolvedValue({ id: 1, email: 'a@example.com', demo: false })
    const { result } = renderHook(() => useAuth())

    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.user).toEqual({ id: 1, email: 'a@example.com', demo: false })
  })

  it('resolves to logged-out on null', async () => {
    vi.mocked(fetchMe).mockResolvedValue(null)
    const { result } = renderHook(() => useAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.user).toBeNull()
  })

  it('treats a failed probe as logged-out, not a crash', async () => {
    vi.mocked(fetchMe).mockRejectedValue(new Error('backend down'))
    const { result } = renderHook(() => useAuth())

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.user).toBeNull()
  })

  it('flips to logged-out when a data route reports session expiry', async () => {
    vi.mocked(fetchMe).mockResolvedValue({ id: 1, email: 'a@example.com', demo: false })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.user).not.toBeNull())

    act(() => {
      window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
    })

    expect(result.current.user).toBeNull()
  })

  it('signOut revokes the session and clears the user', async () => {
    vi.mocked(fetchMe).mockResolvedValue({ id: 1, email: 'a@example.com', demo: false })
    vi.mocked(logout).mockResolvedValue(undefined)
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.user).not.toBeNull())

    await act(() => result.current.signOut())

    expect(logout).toHaveBeenCalled()
    expect(result.current.user).toBeNull()
  })
})
