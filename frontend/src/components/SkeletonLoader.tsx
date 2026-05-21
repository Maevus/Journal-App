interface Props {
  lines?: number
}

export function SkeletonLoader({ lines = 4 }: Props) {
  return (
    <div className="animate-pulse space-y-2" role="status" aria-label="Loading">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className={`h-4 rounded bg-gray-200 ${i === lines - 1 ? 'w-3/4' : 'w-full'}`}
        />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  )
}
