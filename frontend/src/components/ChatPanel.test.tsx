import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from './ChatPanel'
import type { Citation } from '../api/client'

// Controllable stand-in for the chat stream hook.
const chat = vi.hoisted(() => ({
  text: '',
  sources: [] as Citation[],
  error: null as string | null,
  quota: null as string | null,
  rateLimited: null as string | null,
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
  chat.quota = null
  chat.rateLimited = null
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

  it('sends prior turns as history on a follow-up and freezes the first turn', () => {
    chat.text = 'Answer A [1]'
    chat.sources = [{ n: 1, id: 5, date: '2026-05-10', source: 'text', snippet: 'a' }]
    render(<ChatPanel />)

    ask('first question')
    ask('second question')

    // Second Ask carries the finished first turn as real user/assistant messages.
    expect(chat.start).toHaveBeenLastCalledWith([
      { role: 'user', content: 'first question' },
      { role: 'assistant', content: 'Answer A [1]' },
      { role: 'user', content: 'second question' },
    ])
    // Both turns are now on screen (first frozen, second live).
    expect(screen.getByText(/first question/)).toBeInTheDocument()
    expect(screen.getByText(/second question/)).toBeInTheDocument()
  })

  it('does not carry an empty answer into history (Bug #1 — abort/error then ask)', () => {
    // A turn that produced no answer (aborted before any token / errored).
    chat.text = ''
    chat.sources = []
    render(<ChatPanel />)

    ask('first question') // ends with no answer
    ask('second question')

    // No empty assistant message is sent, and no blank turn is frozen.
    expect(chat.start).toHaveBeenLastCalledWith([{ role: 'user', content: 'second question' }])
    expect(screen.queryByText(/first question/)).toBeNull()
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

describe('ChatPanel refusal frames', () => {
  it('shows a friendly notice, not the error alert, when a turn is refused', () => {
    // After an over-budget turn: the question is pending, no answer text came.
    chat.quota = 'daily LLM budget exhausted'
    render(<ChatPanel />)
    ask('how was May?')
    // The backend's own wording is rendered — it knows the budget and reset time.
    expect(screen.getByText('daily LLM budget exhausted')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull() // a refusal is not an error
  })

  it('shows the rate-limit refusal as a notice too', () => {
    chat.rateLimited = 'Too many requests. Try again in 30 seconds.'
    render(<ChatPanel />)
    ask('how was May?')
    expect(screen.getByRole('status')).toHaveTextContent('Try again in 30 seconds')
    expect(screen.queryByRole('alert')).toBeNull() // slowing down is not a failure
  })
})
