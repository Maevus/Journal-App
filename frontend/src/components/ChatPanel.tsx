import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { chatUrl } from '../api/client'
import { useSSE } from '../hooks/useSSE'
import { SkeletonLoader } from './SkeletonLoader'

export function ChatPanel() {
  const [question, setQuestion] = useState('')
  const [asked, setAsked] = useState('')
  // Bumped on every Ask so re-asking the same question re-fires the stream.
  const [askCount, setAskCount] = useState(0)
  const { text, error, isStreaming, start, abort } = useSSE(chatUrl(asked))
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the answer pinned to the bottom while tokens stream in.
  useEffect(() => {
    if (isStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [text, isStreaming])

  // Ask updates `asked` (→ a new chat URL → a fresh `start`), then this effect
  // fires the stream against that URL. `askCount` lets an identical re-ask re-run.
  useEffect(() => {
    if (asked) start()
  }, [askCount, asked, start])

  const handleAsk = () => {
    const q = question.trim()
    if (!q) return
    setAsked(q)
    setAskCount((n) => n + 1)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex gap-2">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAsk()}
          placeholder="Ask your journal anything…"
          className="flex-1 rounded-brutal border-2 border-ink bg-white px-3 py-2 text-sm font-medium placeholder:text-ink/40 focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink"
        />
        {isStreaming ? (
          <button onClick={abort} className="btn-brutal bg-berry px-4 py-2 text-sm text-white">
            Stop
          </button>
        ) : (
          <button onClick={handleAsk} className="btn-brutal bg-gold px-4 py-2 text-sm text-ink">
            Ask
          </button>
        )}
      </div>

      {asked && (
        <p className="mb-3 text-sm font-bold text-ink/70">
          <span className="text-ink/40">Q:</span> {asked}
        </p>
      )}

      {error && (
        <div
          className="mb-3 rounded-brutal border-2 border-ink bg-berry p-3 text-sm font-bold text-white"
          role="alert"
        >
          {error}
        </div>
      )}

      {isStreaming && !text && <SkeletonLoader lines={4} />}

      {text && (
        <div
          ref={scrollRef}
          className="prose prose-sm max-h-[28rem] max-w-none flex-1 overflow-y-auto text-ink prose-headings:font-display"
        >
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      )}

      {!asked && !text && !error && (
        <p className="text-sm font-medium text-ink/50">
          Ask a question about your journal — e.g. &ldquo;what did I write about climbing?&rdquo; or
          &ldquo;how was my mood in May?&rdquo;. Answers are grounded in your entries and cite their
          dates.
        </p>
      )}
    </div>
  )
}
