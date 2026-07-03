import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  chatStreamUrl,
  exportUrl,
  fetchEntriesByDate,
  fetchMe,
  fetchTrends,
  ingestEntries,
  loginUrl,
  logout,
  searchEntries,
  sourceNoteUrl,
  synthesisUrl,
} from './client'

const BASE = 'http://localhost:8000' // VITE_API_BASE_URL is unset under test → default

function mockFetch(body: unknown, ok = true, statusText = 'OK') {
  const fn = vi.fn().mockResolvedValue({
    ok,
    statusText,
    json: async () => body,
  } as Response)
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('synthesisUrl', () => {
  it('builds the default URL with limit and days', () => {
    expect(synthesisUrl()).toBe(`${BASE}/stream/synthesis?limit=20&days=0`)
  })

  it('includes source only when provided', () => {
    expect(synthesisUrl(10, 30, 'google_keep')).toBe(
      `${BASE}/stream/synthesis?limit=10&days=30&source=google_keep`,
    )
    expect(synthesisUrl(10, 30, null)).not.toContain('source')
  })
})

describe('chatStreamUrl', () => {
  it('points at the chat stream endpoint', () => {
    expect(chatStreamUrl()).toBe(`${BASE}/stream/chat`)
  })
})

describe('exportUrl', () => {
  it('builds the export URL with format and optional source', () => {
    expect(exportUrl('csv')).toBe(`${BASE}/export?format=csv`)
    expect(exportUrl('json', 'goodnotes')).toBe(`${BASE}/export?format=json&source=goodnotes`)
    expect(exportUrl('csv', null)).not.toContain('source')
  })
})

describe('sourceNoteUrl', () => {
  it('builds a Keep deep link', () => {
    expect(sourceNoteUrl('google_keep', 'abc123')).toBe(
      'https://keep.google.com/u/0/#NOTE/abc123',
    )
  })

  it('returns null for other sources or missing id', () => {
    expect(sourceNoteUrl('goodnotes', 'x')).toBeNull()
    expect(sourceNoteUrl('google_keep', null)).toBeNull()
  })
})

describe('fetchTrends', () => {
  it('requests the trends endpoint and unwraps the payload', async () => {
    const fn = mockFetch({ trends: [{ date: '2026-05-10', score: 0.5 }] })
    const points = await fetchTrends(30, 'goodnotes')
    expect(points).toEqual([{ date: '2026-05-10', score: 0.5 }])
    expect(fn).toHaveBeenCalledWith(`${BASE}/trends?days=30&source=goodnotes`, { credentials: 'include' })
  })

  it('throws on a non-ok response', async () => {
    mockFetch(null, false, 'Server Error')
    await expect(fetchTrends()).rejects.toThrow(/Trends failed/)
  })
})

describe('fetchEntriesByDate', () => {
  it('requests the entries endpoint for a date', async () => {
    const fn = mockFetch({ entries: [{ id: 1, content: 'hi' }] })
    const entries = await fetchEntriesByDate('2026-05-10')
    expect(entries).toEqual([{ id: 1, content: 'hi' }])
    expect(fn).toHaveBeenCalledWith(`${BASE}/entries?date=2026-05-10`, { credentials: 'include' })
  })
})

describe('searchEntries', () => {
  it('POSTs the query and returns results', async () => {
    const fn = mockFetch({ results: [{ id: 2 }] })
    const data = await searchEntries('climbing', 5)
    expect(data.results).toEqual([{ id: 2 }])
    const [url, init] = fn.mock.calls[0]
    expect(url).toBe(`${BASE}/search`)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ query: 'climbing', limit: 5 })
  })
})

describe('ingestEntries', () => {
  it('POSTs entries with the source', async () => {
    const fn = mockFetch({ ingested: 2, skipped: 0 })
    const stats = await ingestEntries(['a', 'b'], 'text')
    expect(stats).toEqual({ ingested: 2, skipped: 0 })
    const init = fn.mock.calls[0][1]
    expect(JSON.parse(init.body)).toEqual({ entries: ['a', 'b'], source: 'text' })
  })

  it('throws on failure', async () => {
    mockFetch(null, false, 'Bad Request')
    await expect(ingestEntries(['a'])).rejects.toThrow(/Ingest failed/)
  })
})

describe('auth', () => {
  function mockAuthFetch(status: number, body: unknown = null) {
    const fn = vi.fn().mockResolvedValue({
      ok: status < 400,
      status,
      statusText: 'x',
      json: async () => body,
    } as Response)
    vi.stubGlobal('fetch', fn)
    return fn
  }

  it('loginUrl points at the backend OAuth kick-off', () => {
    expect(loginUrl()).toBe(`${BASE}/auth/login`)
  })

  it('fetchMe returns the user and sends the cookie', async () => {
    const fn = mockAuthFetch(200, { id: 1, email: 'a@example.com' })
    const user = await fetchMe()
    expect(user).toEqual({ id: 1, email: 'a@example.com' })
    const [url, init] = fn.mock.calls[0]
    expect(url).toBe(`${BASE}/auth/me`)
    expect(init.credentials).toBe('include')
  })

  it('fetchMe returns null on 401 (logged out, not an error)', async () => {
    mockAuthFetch(401)
    expect(await fetchMe()).toBeNull()
  })

  it('fetchMe throws on non-401 failures', async () => {
    mockAuthFetch(500)
    await expect(fetchMe()).rejects.toThrow(/Auth check failed/)
  })

  it('logout POSTs with the cookie', async () => {
    const fn = mockAuthFetch(204)
    await logout()
    const [url, init] = fn.mock.calls[0]
    expect(url).toBe(`${BASE}/auth/logout`)
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('include')
  })
})
