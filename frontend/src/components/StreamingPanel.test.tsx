import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StreamingPanel } from './StreamingPanel'

// Controllable stand-in for the SSE hook.
const sse = vi.hoisted(() => ({
  text: '',
  error: null as string | null,
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

  it('renders the streamed text and starts on click', () => {
    sse.text = 'Synthesis of your month'
    render(<StreamingPanel range={range} source={allSources} />)
    expect(screen.getByText(/Synthesis of your month/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /generate synthesis/i }))
    expect(sse.start).toHaveBeenCalled()
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
