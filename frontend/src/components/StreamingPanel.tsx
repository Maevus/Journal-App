import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { synthesisUrl } from '../api/client'
import { useSSE } from '../hooks/useSSE'
import { SkeletonLoader } from './SkeletonLoader'
import type { SourceFilter } from './SourceSelector'
import type { TimeRange } from './TimeRangeSelector'

interface StreamingPanelProps {
  range: TimeRange
  source: SourceFilter
}

export function StreamingPanel({ range, source }: StreamingPanelProps) {
  const { text, error, isStreaming, start, abort } = useSSE(
    synthesisUrl(20, range.days, source.value),
  )
  const scopeLabel = source.value ? `${range.label} · ${source.label}` : range.label
  // Which period/source the visible text was generated for — synthesis costs an
  // LLM call, so changing the scope flags the text as stale instead of auto-rerunning.
  const [generatedFor, setGeneratedFor] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the scroll pinned to the bottom while tokens stream in.
  useEffect(() => {
    if (isStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [text, isStreaming])

  const handleStart = () => {
    setGeneratedFor(scopeLabel)
    start()
  }

  const stale = text !== '' && generatedFor !== null && generatedFor !== scopeLabel

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex items-center justify-between">
        <span className="font-display text-xl font-bold lowercase">
          {scopeLabel.toLowerCase()}
        </span>
        {isStreaming ? (
          <button onClick={abort} className="btn-brutal bg-berry px-3 py-1 text-sm text-white">
            Stop generating
          </button>
        ) : (
          <button
            onClick={handleStart}
            className="btn-brutal bg-lilac px-3 py-1 text-sm text-white"
          >
            {stale ? 'Regenerate' : 'Generate synthesis'}
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

      {stale && !isStreaming && (
        <div
          className="mb-3 rounded-brutal border-2 border-ink bg-gold p-3 text-sm font-medium"
          role="status"
        >
          This synthesis covers <strong>{generatedFor?.toLowerCase()}</strong> — regenerate to
          reflect {scopeLabel.toLowerCase()}.
        </div>
      )}

      {isStreaming && !text && <SkeletonLoader lines={5} />}

      {text && (
        <div
          ref={scrollRef}
          className="prose prose-sm max-h-[32rem] max-w-none flex-1 overflow-y-auto text-ink prose-headings:font-display"
        >
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      )}

      {!text && !isStreaming && !error && (
        <p className="text-sm font-medium text-ink/50">
          Click &ldquo;Generate synthesis&rdquo; to analyse your journal entries from{' '}
          {range.label === 'All time' ? 'all time' : `the last ${range.label.toLowerCase()}`}.
        </p>
      )}
    </div>
  )
}
