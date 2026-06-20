import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from './ChatPanel'

// Controllable stand-in for the SSE hook (mirrors StreamingPanel.test.tsx).
const sse = vi.hoisted(() => ({
  text: '',
  error: null as string | null,
  isStreaming: false,
  start: vi.fn(),
  abort: vi.fn(),
}))

vi.mock('../hooks/useSSE', () => ({ useSSE: () => sse }))

beforeEach(() => {
  sse.text = ''
  sse.error = null
  sse.isStreaming = false
  sse.start.mockClear()
  sse.abort.mockClear()
})

describe('ChatPanel', () => {
  it('shows the empty-state prompt initially and does not stream', () => {
    render(<ChatPanel />)
    expect(screen.getByRole('button', { name: /ask/i })).toBeInTheDocument()
    expect(screen.getByText(/ask a question about your journal/i)).toBeInTheDocument()
    expect(sse.start).not.toHaveBeenCalled()
  })

  it('starts the stream and shows the question on Ask', () => {
    render(<ChatPanel />)
    fireEvent.change(screen.getByPlaceholderText(/ask your journal/i), {
      target: { value: 'what did I write about climbing' },
    })
    fireEvent.click(screen.getByRole('button', { name: /ask/i }))
    expect(sse.start).toHaveBeenCalled()
    expect(screen.getByText(/what did I write about climbing/)).toBeInTheDocument()
  })

  it('does not stream on a blank question', () => {
    render(<ChatPanel />)
    fireEvent.change(screen.getByPlaceholderText(/ask your journal/i), {
      target: { value: '   ' },
    })
    fireEvent.click(screen.getByRole('button', { name: /ask/i }))
    expect(sse.start).not.toHaveBeenCalled()
  })

  it('renders the streamed answer and a stop button while streaming', () => {
    sse.text = 'You went climbing on 2026-05-10.'
    sse.isStreaming = true
    render(<ChatPanel />)
    expect(screen.getByText(/You went climbing on 2026-05-10/)).toBeInTheDocument()
    const stop = screen.getByRole('button', { name: /stop/i })
    fireEvent.click(stop)
    expect(sse.abort).toHaveBeenCalled()
  })
})
