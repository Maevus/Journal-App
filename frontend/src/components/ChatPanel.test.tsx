import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from './ChatPanel'
import type { Citation } from '../api/client'

// Controllable stand-in for the chat stream hook.
const chat = vi.hoisted(() => ({
  text: '',
  sources: [] as Citation[],
  error: null as string | null,
  isStreaming: false,
  start: vi.fn(),
  abort: vi.fn(),
}))

vi.mock('../hooks/useChatStream', () => ({ useChatStream: () => chat }))
// Stub DayDetail so we can assert it opened without exercising its fetch.
vi.mock('./DayDetail', () => ({
  DayDetail: ({ date }: { date: string }) => <div>DayDetail for {date}</div>,
}))

function ask(question: string) {
  fireEvent.change(screen.getByPlaceholderText(/ask your journal/i), {
    target: { value: question },
  })
  fireEvent.click(screen.getByRole('button', { name: /^ask$/i }))
}

beforeEach(() => {
  chat.text = ''
  chat.sources = []
  chat.error = null
  chat.isStreaming = false
  chat.start.mockClear()
  chat.abort.mockClear()
})

describe('ChatPanel', () => {
  it('shows the empty-state hint before any question', () => {
    render(<ChatPanel />)
    expect(screen.getByText(/grounded in your entries/i)).toBeInTheDocument()
  })

  it('starts a stream with the question and renders the frozen answer', () => {
    chat.text = 'You went climbing [1]'
    chat.sources = [{ n: 1, id: 5, date: '2026-05-10', source: 'text', snippet: 'gym' }]
    render(<ChatPanel />)

    ask('what about climbing')

    expect(chat.start).toHaveBeenCalledWith([{ role: 'user', content: 'what about climbing' }])
    expect(screen.getByText(/You went climbing/)).toBeInTheDocument()
  })

  it('renders a chip only for cited entries and opens DayDetail on click', () => {
    chat.text = 'You went climbing [1] and rested.'
    chat.sources = [
      { n: 1, id: 5, date: '2026-05-10', source: 'text', snippet: 'gym' },
      { n: 2, id: 6, date: '2026-05-12', source: 'goodnotes', snippet: 'rest' },
    ]
    render(<ChatPanel />)
    ask('what about climbing')

    // [2] was never cited in the prose → no chip for it
    expect(screen.queryByText(/2026-05-12/)).toBeNull()
    const chip = screen.getByRole('button', { name: /2026-05-10/ })
    fireEvent.click(chip)
    expect(screen.getByText('DayDetail for 2026-05-10')).toBeInTheDocument()
  })

  it('Clear resets the conversation', () => {
    chat.text = 'An answer [1]'
    chat.sources = [{ n: 1, id: 5, date: '2026-05-10', source: 'text', snippet: 'x' }]
    render(<ChatPanel />)
    ask('a question')
    expect(screen.getByText(/An answer/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /clear/i }))
    expect(screen.queryByText(/An answer/)).toBeNull()
    expect(screen.getByText(/grounded in your entries/i)).toBeInTheDocument()
  })
})
