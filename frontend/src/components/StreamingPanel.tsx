import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { synthesisUrl } from '../api/client'
import { useSSE } from '../hooks/useSSE'
import { SkeletonLoader } from './SkeletonLoader'
import type { TimeRange } from './TimeRangeSelector'

interface StreamingPanelProps {
  range: TimeRange
}

export function StreamingPanel({ range }: StreamingPanelProps) {
  const { text, error, isStreaming, start, abort } = useSSE(synthesisUrl(20, range.days))
  // Which period the visible text was generated for — synthesis costs an LLM
  // call, so changing the period flags the text as stale instead of auto-rerunning.
  const [generatedFor, setGeneratedFor] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Keep the scroll pinned to the bottom while tokens stream in.
  useEffect(() => {
    if (isStreaming && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [text, isStreaming])

  const handleStart = () => {
    setGeneratedFor(range.label)
    start()
  }

  const stale = text !== '' && generatedFor !== null && generatedFor !== range.label

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900">
          Journal Synthesis
          <span className="ml-2 text-sm font-normal text-gray-400">
            {range.label.toLowerCase()}
          </span>
        </h2>
        {isStreaming ? (
          <button
            onClick={abort}
            className="rounded bg-red-100 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-200 focus:outline-none focus:ring-2 focus:ring-red-400"
          >
            Stop generating
          </button>
        ) : (
          <button
            onClick={handleStart}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            {stale ? 'Regenerate' : 'Generate synthesis'}
          </button>
        )}
      </div>

      {error && (
        <div className="mb-3 rounded-md bg-red-50 p-3 text-sm text-red-700" role="alert">
          {error}
        </div>
      )}

      {stale && !isStreaming && (
        <div className="mb-3 rounded-md bg-amber-50 p-3 text-sm text-amber-700" role="status">
          This synthesis covers <strong>{generatedFor?.toLowerCase()}</strong> — regenerate to
          reflect {range.label.toLowerCase()}.
        </div>
      )}

      {isStreaming && !text && <SkeletonLoader lines={5} />}

      {text && (
        <div
          ref={scrollRef}
          className="prose prose-sm max-h-[32rem] max-w-none flex-1 overflow-y-auto text-gray-800"
        >
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      )}

      {!text && !isStreaming && !error && (
        <p className="text-sm text-gray-400">
          Click &ldquo;Generate synthesis&rdquo; to analyse your journal entries from{' '}
          {range.label === 'All time' ? 'all time' : `the last ${range.label.toLowerCase()}`}.
        </p>
      )}
    </div>
  )
}
