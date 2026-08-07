import { useEffect, useState, type ReactNode } from 'react'
import { fetchKeyStatus } from '../api/client'
import { ApiKeyPanel } from './ApiKeyPanel'
import { DeleteAccountPanel } from './DeleteAccountPanel'
import { SkeletonLoader } from './SkeletonLoader'
import { UsagePanel } from './UsagePanel'

type Tab = 'usage' | 'key' | 'delete'

/** Usage, API key and account deletion folded into one card with sticker tabs.
 *  Opens on the API-key tab only for a signed-in user with no key yet;
 *  otherwise Usage. Never opens on Delete. */
export function AccountPanel({ demo }: { demo: boolean }) {
  // Demo knows its default synchronously; a real user needs a key-status probe
  // first, so `null` shows a brief skeleton instead of flashing the wrong tab.
  const [tab, setTab] = useState<Tab | null>(demo ? 'usage' : null)

  useEffect(() => {
    if (demo) return // shared account: no key management, always open on Usage
    let alive = true
    fetchKeyStatus()
      .then((s) => alive && setTab(s.has_key ? 'usage' : 'key'))
      .catch(() => alive && setTab('usage')) // probe failed → the safe default
    return () => {
      alive = false
    }
  }, [demo])

  return (
    <section className="relative mt-8">
      <div role="tablist" aria-label="Account" className="absolute -top-3 left-6 z-10 flex gap-2">
        <TabSticker id="usage" active={tab === 'usage'} onClick={() => setTab('usage')}>
          Usage
        </TabSticker>
        <TabSticker id="key" active={tab === 'key'} onClick={() => setTab('key')}>
          API key
        </TabSticker>
        <TabSticker
          id="delete"
          active={tab === 'delete'}
          tone="danger"
          onClick={() => setTab('delete')}
        >
          Delete account
        </TabSticker>
      </div>
      <div
        className="card-brutal p-6 pt-8"
        role="tabpanel"
        aria-labelledby={tab ? `tab-${tab}` : undefined}
      >
        {tab === null ? (
          <SkeletonLoader lines={6} />
        ) : tab === 'usage' ? (
          <UsagePanel demo={demo} />
        ) : tab === 'key' ? (
          <ApiKeyPanel demo={demo} />
        ) : (
          <DeleteAccountPanel demo={demo} />
        )}
      </div>
    </section>
  )
}

/** A sticker label doubling as a tab: coloured when selected, white when not.
 *  Un-rotated, unlike the decorative stickers, so the set reads as a tab bar.
 *  `danger` selects in berry — the palette's stop colour. */
function TabSticker({
  id,
  active,
  tone = 'default',
  onClick,
  children,
}: {
  id: string
  active: boolean
  tone?: 'default' | 'danger'
  onClick: () => void
  children: ReactNode
}) {
  const selected = tone === 'danger' ? 'bg-berry text-white' : 'bg-gold text-ink'
  return (
    <button
      role="tab"
      id={`tab-${id}`}
      aria-selected={active}
      onClick={onClick}
      className={`sticker px-3 py-1 text-xs transition-colors focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink ${
        active ? selected : 'bg-white text-ink/40 hover:text-ink/70'
      }`}
    >
      {children}
    </button>
  )
}
