import { useState } from 'react'
import './App.css'
import { exportUrl } from './api/client'
import { AccountPanel } from './components/AccountPanel'
import { AuthWidget } from './components/AuthWidget'
import { ChatPanel } from './components/ChatPanel'
import { DemoBanner } from './components/DemoBanner'
import { LoginGate } from './components/LoginGate'
import { MoodChart } from './components/MoodChart'
import { SearchPanel } from './components/SearchPanel'
import { StreamingPanel } from './components/StreamingPanel'
import { SOURCE_FILTERS, SourceSelector } from './components/SourceSelector'
import { TIME_RANGES, TimeRangeSelector } from './components/TimeRangeSelector'
import { useAuth } from './hooks/useAuth'

function App() {
  // Single useAuth instance: the gate and header widget share one login
  // state, so a mid-session 401 (AUTH_EXPIRED_EVENT) flips the whole tree.
  const { user, loading, signOut } = useAuth()
  // One time period + source filter drives the mood chart, day drill-down,
  // and the synthesis panel.
  const [range, setRange] = useState(TIME_RANGES[0])
  const [source, setSource] = useState(SOURCE_FILTERS[0])

  if (loading) return null // don't flash the gate (or the dashboard) mid-probe
  if (!user) return <LoginGate />

  return (
    <div className="min-h-screen bg-paper font-sans text-ink">
      {user.demo && <DemoBanner />}
      <header className="border-b-4 border-ink bg-white px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
          <div>
            <h1 className="font-display text-2xl font-bold uppercase tracking-tight sm:text-3xl">
              <span className="inline-block -rotate-1 bg-rose px-2">Woolgather</span>
            </h1>
            <p className="mt-1 text-sm font-medium text-ink/60">
              Patterns from your personal journal
            </p>
          </div>
          <div className="flex flex-col items-start gap-2 sm:items-end">
            <AuthWidget user={user} onSignOut={() => void signOut()} />
            <TimeRangeSelector value={range} onChange={setRange} />
            <SourceSelector value={source} onChange={setSource} />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-bold uppercase tracking-wide text-ink/60">Export</span>
              <a
                href={exportUrl('csv', source.value)}
                download
                className="btn-brutal bg-white px-2 py-0.5 text-xs"
              >
                CSV
              </a>
              <a
                href={exportUrl('json', source.value)}
                download
                className="btn-brutal bg-white px-2 py-0.5 text-xs"
              >
                JSON
              </a>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        {/* Full-bleed trends section, sticker label overlapping the frame */}
        <section className="relative mb-8">
          <span className="sticker absolute -top-3 left-6 z-10 -rotate-1 bg-rose px-3 py-1 text-xs">
            Mood trends
          </span>
          <div className="card-brutal p-6 pt-8">
            {/* key={range.days}: picking a preset remounts the chart, clearing
                any custom From/To window without a set-state-in-effect. */}
            <MoodChart key={range.days} range={range} source={source} />
          </div>
        </section>

        {/* Intentionally asymmetric: synthesis carries more visual weight */}
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-5">
          <section className="relative lg:col-span-3">
            <span className="sticker absolute -top-3 left-6 z-10 rotate-1 bg-lilac px-3 py-1 text-xs text-white">
              Synthesis
            </span>
            <div className="card-brutal h-full p-6 pt-8">
              <StreamingPanel range={range} source={source} />
            </div>
          </section>
          <section className="relative lg:col-span-2">
            <span className="sticker absolute -top-3 left-6 z-10 -rotate-1 bg-mint px-3 py-1 text-xs">
              Search
            </span>
            <div className="card-brutal h-full p-6 pt-8">
              <SearchPanel />
            </div>
          </section>
        </div>

        {/* Ask: single-turn RAG over the whole journal, full-width below the grid */}
        <section className="relative mt-8">
          <span className="sticker absolute -top-3 left-6 z-10 rotate-1 bg-gold px-3 py-1 text-xs">
            Ask
          </span>
          <div className="card-brutal p-6 pt-8">
            <ChatPanel />
          </div>
        </section>

        {/* Account: spend and BYO key, tabbed into one card. Saving/removing
            the key refetches usage via KEY_CHANGED_EVENT. */}
        <AccountPanel demo={user.demo} />
      </main>
    </div>
  )
}

export default App
