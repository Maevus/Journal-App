import { useEffect, useState } from 'react'
import { deleteApiKey, fetchKeyStatus, saveApiKey } from '../api/client'

/** Bring-your-own-key control (F4). Free-tier users run Haiku; adding an
 *  Anthropic key here (validated against Anthropic on save, then stored
 *  encrypted server-side) upgrades their analysis to Opus on their own key.
 *  The key is entered into a password field and never rendered back — the
 *  status endpoint returns only a boolean. In demo mode (`demo`) the whole
 *  control is replaced by a note: every visitor shares the demo account, so
 *  POST /keys would 403 anyway — better to say so than to offer a form. */
export function ApiKeyPanel({ demo = false }: { demo?: boolean }) {
  const [hasKey, setHasKey] = useState<boolean | null>(null) // null = still loading
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (demo) return // no key status to probe — the demo account can't hold one
    let alive = true
    fetchKeyStatus()
      .then((s) => alive && setHasKey(s.has_key))
      // A status read failure just falls back to the add-key form; the save
      // itself surfaces any real error.
      .catch(() => alive && setHasKey(false))
    return () => {
      alive = false
    }
  }, [demo])

  const save = async () => {
    const key = apiKey.trim()
    if (!key) return
    setBusy(true)
    setError(null)
    try {
      await saveApiKey(key) // validated against Anthropic server-side
      setApiKey('')
      setHasKey(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save the key')
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    setError(null)
    try {
      await deleteApiKey()
      setHasKey(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the key')
    } finally {
      setBusy(false)
    }
  }

  if (demo) {
    return (
      <p className="text-sm font-medium text-ink/70">
        The demo runs on a shared account, so key management is disabled here. In the real
        app, adding your own Anthropic key runs analysis on Opus with no daily cap.
      </p>
    )
  }

  if (hasKey === null) {
    return <p className="text-sm font-medium text-ink/50">Checking your key…</p>
  }

  return (
    <div className="flex flex-col gap-3">
      {hasKey ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-brutal border-2 border-ink bg-mint px-2 py-0.5 text-xs font-bold uppercase tracking-wide">
            Your key · Opus
          </span>
          <p className="min-w-0 flex-1 text-sm font-medium text-ink/70">
            Synthesis, chat, and mood scoring run on your own Anthropic key.
          </p>
          <button
            onClick={() => void remove()}
            disabled={busy}
            className="btn-brutal bg-white px-3 py-1 text-sm disabled:opacity-50"
          >
            {busy ? 'Removing…' : 'Remove key'}
          </button>
        </div>
      ) : (
        <>
          <p className="text-sm font-medium text-ink/70">
            You're on the free tier (Haiku). Add your own Anthropic API key to run Opus — it's
            validated on save and stored encrypted, never shown again.
          </p>
          <div className="flex flex-wrap gap-2">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && void save()}
              placeholder="sk-ant-…"
              autoComplete="off"
              className="min-w-0 flex-1 rounded-brutal border-2 border-ink bg-white px-3 py-2 text-sm font-medium placeholder:text-ink/40 focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink"
            />
            <button
              onClick={() => void save()}
              disabled={busy || !apiKey.trim()}
              className="btn-brutal bg-gold px-4 py-2 text-sm disabled:opacity-50"
            >
              {busy ? 'Validating…' : 'Save key'}
            </button>
          </div>
        </>
      )}
      {error && <p className="text-sm font-bold text-berry">{error}</p>}
    </div>
  )
}
