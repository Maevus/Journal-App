import ReactMarkdown from 'react-markdown'
import { synthesisUrl } from '../api/client'
import { useSSE } from '../hooks/useSSE'
import { SkeletonLoader } from './SkeletonLoader'

export function StreamingPanel() {
  const { text, error, isStreaming, start, abort } = useSSE(synthesisUrl())

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-gray-900">Journal Synthesis</h2>
        {isStreaming ? (
          <button
            onClick={abort}
            className="rounded bg-red-100 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-200 focus:outline-none focus:ring-2 focus:ring-red-400"
          >
            Stop generating
          </button>
        ) : (
          <button
            onClick={start}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            Generate synthesis
          </button>
        )}
      </div>

      {error && (
        <div className="mb-3 rounded-md bg-red-50 p-3 text-sm text-red-700" role="alert">
          {error}
        </div>
      )}

      {isStreaming && !text && <SkeletonLoader lines={5} />}

      {text && (
        <div className="prose prose-sm max-w-none flex-1 overflow-y-auto text-gray-800">
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      )}

      {!text && !isStreaming && !error && (
        <p className="text-sm text-gray-400">
          Click &ldquo;Generate synthesis&rdquo; to analyse your recent journal entries.
        </p>
      )}
    </div>
  )
}
