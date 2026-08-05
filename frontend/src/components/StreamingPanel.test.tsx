import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StreamingPanel } from './StreamingPanel'

// Controllable stand-in for the synthesis stream hook.
const sse = vi.hoisted(() => ({
  text: '',
  error: null as string | null,
  quota: null as string | null,
  rateLimited: null as string | null,
  isStreaming: false,
  start: vi.fn(),
  abort: vi.fn(),
}))

vi.mock('../hooks/useSSE', () => ({ useSSE: () => sse }))

const range = { days: 30, label: '30 days' }
const allSources = { value: null, label: 'All' }

beforeEach(() => {
  sse.text = ''
  sse.error = null
  sse.quota = null
  sse.rateLimited = null
  sse.isStreaming = false
  sse.start.mockClear()
  sse.abort.mockClear()
})

describe('StreamingPanel', () => {
  it('shows the generate prompt when empty', () => {
    render(<StreamingPanel range={range} source={allSources} />)
    expect(screen.getByRole('button', { name: /generate synthesis/i })).toBeInTheDocument()
    expect(screen.getByText(/to analyse your journal entries/i)).toBeInTheDocument()
  })

  it('shows a stop button while streaming', () => {
    sse.isStreaming = true
    render(<StreamingPanel range={range} source={allSources} />)
    const stop = screen.getByRole('button', { name: /stop generating/i })
    fireEvent.click(stop)
    expect(sse.abort).toHaveBeenCalled()
  })

  it('renders the streamed text and starts with the current scope', () => {
    sse.text = 'Synthesis of your month'
    render(<StreamingPanel range={range} source={{ value: 'google_keep', label: 'Keep' }} />)
    expect(screen.getByText(/Synthesis of your month/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /generate synthesis/i }))
    // The scope rides in the POST body, not a URL, so the panel passes it to start().
    expect(sse.start).toHaveBeenCalledWith({ limit: 20, days: 30, source: 'google_keep' })
  })

  it('shows a friendly notice, not the error alert, on a quota refusal', () => {
    sse.quota = 'daily LLM budget exhausted'
    render(<StreamingPanel range={range} source={allSources} />)
    // The backend's own wording is rendered — it knows the budget and reset time.
    expect(screen.getByText('daily LLM budget exhausted')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull() // a refusal is not an error
  })

  it('shows the rate-limit refusal as a notice too', () => {
    sse.rateLimited = 'Too many requests. Try again in 30 seconds.'
    render(<StreamingPanel range={range} source={allSources} />)
    expect(screen.getByRole('status')).toHaveTextContent('Try again in 30 seconds')
    expect(screen.queryByRole('alert')).toBeNull() // slowing down is not a failure
  })

  it('flags the text stale when the scope changes after generating', () => {
    sse.text = 'Existing synthesis'
    const { rerender } = render(<StreamingPanel range={range} source={allSources} />)
    // Generate for the current scope, then switch source → stale.
    fireEvent.click(screen.getByRole('button', { name: /generate synthesis/i }))
    expect(screen.queryByRole('status')).toBeNull()

    rerender(<StreamingPanel range={range} source={{ value: 'google_keep', label: 'Keep' }} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /regenerate/i })).toBeInTheDocument()
  })
})
