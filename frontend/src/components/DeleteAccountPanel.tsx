import { useState } from 'react'
import { AUTH_EXPIRED_EVENT, DELETE_CONFIRMATION, deleteAccount } from '../api/client'

/** Erase the account and everything in it. The button unlocks only once the
 *  user types the confirmation word, so no single click reaches the route. */
export function DeleteAccountPanel({ demo = false }: { demo?: boolean }) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Rows deleted per table, or null while the account still exists.
  const [deleted, setDeleted] = useState<Record<string, number> | null>(null)

  const confirmed = typed.trim() === DELETE_CONFIRMATION

  const remove = async () => {
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      setDeleted(await deleteAccount())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not delete the account')
    } finally {
      setBusy(false)
    }
  }

  if (demo) {
    return (
      <p className="text-sm font-medium text-ink/70">
        The demo runs on a shared account, so it can't be deleted — one visitor would empty
        the demo for everyone. In the real app this erases your journal and everything
        derived from it.
      </p>
    )
  }

  if (deleted) {
    const entries = deleted.journalentry ?? 0
    return (
      <div className="flex flex-col items-start gap-3" role="status">
        <p className="text-sm font-medium text-ink/70">
          Your account is gone, along with {entries} journal{' '}
          {entries === 1 ? 'entry' : 'entries'} and everything derived from them.
        </p>
        <button
          onClick={() => window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))}
          className="btn-brutal bg-white px-4 py-2 text-sm"
        >
          Return to sign-in
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm font-medium text-ink/70">
        This erases your journal entries, their embeddings, your stored API key, and your
        usage history. It happens immediately and cannot be undone — export your data from
        the header first if you want a copy.
      </p>
      <label htmlFor="delete-confirm" className="text-sm font-medium text-ink/70">
        Type <span className="font-bold text-ink">{DELETE_CONFIRMATION}</span> to confirm.
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id="delete-confirm"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void remove()}
          autoComplete="off"
          className="min-w-0 flex-1 rounded-brutal border-2 border-ink bg-white px-3 py-2 text-sm font-medium placeholder:text-ink/40 focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink"
        />
        <button
          onClick={() => void remove()}
          disabled={busy || !confirmed}
          className="btn-brutal bg-berry px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {busy ? 'Deleting…' : 'Delete my account'}
        </button>
      </div>
      {error && <p className="text-sm font-bold text-berry">{error}</p>}
    </div>
  )
}
