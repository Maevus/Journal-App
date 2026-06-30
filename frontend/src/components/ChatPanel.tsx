import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { type ChatMessage, type Citation } from '../api/client'
import { useChatStream } from '../hooks/useChatStream'
import { DayDetail } from './DayDetail'
import { SkeletonLoader } from './SkeletonLoader'
import type { SourceFilter } from './SourceSelector'

interface Turn {
  question: string
  answer: string
  sources: Citation[]
}

// The entries the answer actually referenced — only [n] markers present in the
// prose become chips (the model is told to cite only what it used).
function usedCitations(answer: string, sources: Citation[]): Citation[] {
  const used = new Set<number>()
  for (const m of answer.matchAll(/\[(\d+)\]/g)) used.add(Number(m[1]))
  return sources.filter((s) => used.has(s.n))
}

function CitationChips({
  answer,
  sources,
  onOpen,
}: {
  answer: string
  sources: Citation[]
  onOpen: (date: string, source: string) => void
}) {
  const chips = usedCitations(answer, sources)
  if (chips.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {chips.map((c) => (
        <button
          key={c.n}
          onClick={() => onOpen(c.date, c.source)}
          title={c.snippet}
          className="btn-brutal bg-white px-2 py-0.5 text-xs text-ink/70 hover:text-ink"
        >
          <span className="font-bold text-ink/40">[{c.n}]</span> {c.date} · {c.source}
        </button>
      ))}
    </div>
  )
}

export function ChatPanel() {
  const [question, setQuestion] = useState('')
  const [conversation, setConversation] = useState<Turn[]>([])
  // The question currently being answered (rendered live until it freezes into a Turn).
  const [pending, setPending] = useState<string | null>(null)
  const [selected, setSelected] = useState<{ date: string; source: string } | null>(null)
  const { text, sources, error, isStreaming, start, abort } = useChatStream()
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the latest answer pinned to the bottom while tokens stream in.
  useEffect(() => {
    if (isStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [text, isStreaming])

  const handleAsk = () => {
    const q = question.trim()
    if (!q || isStreaming) return
    // The current turn renders live from the hook. When a new question is asked,
    // freeze the just-finished turn into history first (in this event handler —
    // not an effect), then start the next stream.
    const priorPair: ChatMessage[] =
      pending !== null ? [
        { role: 'user', content: pending },
        { role: 'assistant', content: text },
      ] : []
    const messages: ChatMessage[] = [
      ...conversation.flatMap((t): ChatMessage[] => [
        { role: 'user', content: t.question },
        { role: 'assistant', content: t.answer },
      ]),
      ...priorPair,
      { role: 'user', content: q },
    ]
    if (pending !== null) {
      setConversation((prev) => [...prev, { question: pending, answer: text, sources }])
    }
    setPending(q)
    setQuestion('')
    start(messages)
  }

  const handleClear = () => {
    abort()
    setConversation([])
    setPending(null)
    setSelected(null)
  }

  const openCitation = (date: string, source: string) => setSelected({ date, source })
  const selectedSource: SourceFilter | null = selected
    ? { label: selected.source, value: selected.source }
    : null

  const hasContent = conversation.length > 0 || pending !== null

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
        {hasContent && !isStreaming && (
          <button onClick={handleClear} className="btn-brutal bg-white px-4 py-2 text-sm text-ink/60 hover:text-ink">
            Clear
          </button>
        )}
      </div>

      {error && (
        <div
          className="mb-3 rounded-brutal border-2 border-ink bg-berry p-3 text-sm font-bold text-white"
          role="alert"
        >
          {error}
        </div>
      )}

      {hasContent && (
        <div ref={scrollRef} className="max-h-[28rem] flex-1 space-y-4 overflow-y-auto">
          {conversation.map((turn, i) => (
            <div key={i}>
              <p className="mb-1 text-sm font-bold text-ink/70">
                <span className="text-ink/40">Q:</span> {turn.question}
              </p>
              <div className="prose prose-sm max-w-none text-ink prose-headings:font-display">
                <ReactMarkdown>{turn.answer}</ReactMarkdown>
              </div>
              <CitationChips answer={turn.answer} sources={turn.sources} onOpen={openCitation} />
            </div>
          ))}

          {pending !== null && (
            <div>
              <p className="mb-1 text-sm font-bold text-ink/70">
                <span className="text-ink/40">Q:</span> {pending}
              </p>
              {isStreaming && !text && <SkeletonLoader lines={4} />}
              {text && (
                <>
                  <div className="prose prose-sm max-w-none text-ink prose-headings:font-display">
                    <ReactMarkdown>{text}</ReactMarkdown>
                  </div>
                  <CitationChips answer={text} sources={sources} onOpen={openCitation} />
                </>
              )}
            </div>
          )}
        </div>
      )}

      {!hasContent && !error && (
        <p className="text-sm font-medium text-ink/50">
          Ask a question about your journal — e.g. &ldquo;what did I write about climbing?&rdquo; or
          &ldquo;how was my mood in May?&rdquo;. Answers are grounded in your entries; tap a citation
          to open that day. Follow-ups keep the conversation&rsquo;s context.
        </p>
      )}

      {selected && selectedSource && (
        <DayDetail date={selected.date} source={selectedSource} onClose={() => setSelected(null)} />
      )}
    </div>
  )
}
