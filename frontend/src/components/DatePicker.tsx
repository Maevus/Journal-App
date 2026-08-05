import { useEffect, useRef, useState } from 'react'

/* Calendar popover replacing native <input type="date">: unthemeable, with a
   dead-end month/year drill-down. Header button zooms out (days → months →
   years); picking a cell zooms back in. */

const MONTHS = [
  'jan', 'feb', 'mar', 'apr', 'may', 'jun',
  'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
] as const
const WEEKDAYS = ['mo', 'tu', 'we', 'th', 'fr', 'sa', 'su'] // Monday-first, like the dd-mm formats

const ddmmyyyy = (iso: string) => `${iso.slice(8, 10)}-${iso.slice(5, 7)}-${iso.slice(0, 4)}`
const pad = (n: number) => String(n).padStart(2, '0')
const toISO = (y: number, m: number, d: number) => `${y}-${pad(m + 1)}-${pad(d)}`
// Slice, don't `new Date(iso)`: UTC-midnight parsing shifts the month in western TZs
const parseYM = (iso: string) => ({ year: Number(iso.slice(0, 4)), month: Number(iso.slice(5, 7)) - 1 })
const lastDay = (y: number, m: number) => new Date(y, m + 1, 0).getDate()

type View = 'days' | 'months' | 'years'

// Shared flat-cell chrome; colour/weight stay per-cell so variants don't clash
const cellBase =
  'rounded-brutal text-sm focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink disabled:pointer-events-none disabled:opacity-30'
const cellSelected = 'border-2 border-ink bg-rose font-bold text-ink shadow-brutal-sm'

interface DatePickerProps {
  label: string // accessible name of the trigger, e.g. "Range start"
  value: string | null // ISO "YYYY-MM-DD"
  onChange: (iso: string) => void
  min?: string
  max?: string
}

export function DatePicker({ label, value, onChange, min, max }: DatePickerProps) {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<View>('days')
  const [cursor, setCursor] = useState({ year: 0, month: 0 }) // set on open
  const containerRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onDocMouseDown = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocMouseDown)
    return () => document.removeEventListener('mousedown', onDocMouseDown)
  }, [open])

  const openPicker = () => {
    const now = new Date()
    setCursor(value ? parseYM(value) : { year: now.getFullYear(), month: now.getMonth() })
    setView('days')
    setOpen(true)
  }

  const select = (iso: string) => {
    onChange(iso)
    setOpen(false)
  }

  // ◀ ▶ step one month / one year / one 12-year block, depending on the view
  const stepUnit = view === 'days' ? 'month' : view === 'months' ? 'year' : 'years'
  const step = (dir: -1 | 1) => {
    if (view === 'days') {
      const m = cursor.month + dir
      setCursor({ year: cursor.year + Math.floor(m / 12), month: ((m % 12) + 12) % 12 })
    } else {
      setCursor({ ...cursor, year: cursor.year + dir * (view === 'months' ? 1 : 12) })
    }
  }

  const now = new Date()
  const todayISO = toISO(now.getFullYear(), now.getMonth(), now.getDate())
  const selectedYM = value ? parseYM(value) : null

  // Fixed 6-week grid (stable popover height), padded with adjacent months
  const lead = (new Date(cursor.year, cursor.month, 1).getDay() + 6) % 7
  const dayCells = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(cursor.year, cursor.month, i + 1 - lead)
    return {
      iso: toISO(d.getFullYear(), d.getMonth(), d.getDate()),
      day: d.getDate(),
      inMonth: d.getMonth() === cursor.month,
    }
  })

  const blockStart = cursor.year - (cursor.year % 12)

  return (
    <div
      ref={containerRef}
      className="relative"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          setOpen(false)
          triggerRef.current?.focus()
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openPicker())}
        className="rounded-brutal border-2 border-ink bg-white px-2 py-1 text-sm font-medium focus:outline-none focus-visible:outline-2 focus-visible:outline-dashed focus-visible:outline-ink"
      >
        {value ? (
          <span className="text-ink">{ddmmyyyy(value)}</span>
        ) : (
          <span className="text-ink/40">dd-mm-yyyy</span>
        )}
        <span aria-hidden className="ml-1.5 text-ink/40">
          ▾
        </span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label={`${label} calendar`}
          className="absolute right-0 top-full z-20 mt-2 w-72 rounded-brutal border-2 border-ink bg-white p-3 shadow-brutal"
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <button
              type="button"
              aria-label={`Previous ${stepUnit}`}
              onClick={() => step(-1)}
              className="btn-brutal bg-white px-2 py-0.5 text-xs"
            >
              ◀
            </button>
            {view === 'years' ? (
              <span className="font-display text-sm font-bold lowercase">
                {blockStart} – {blockStart + 11}
              </span>
            ) : (
              <button
                type="button"
                title="Zoom out"
                onClick={() => setView(view === 'days' ? 'months' : 'years')}
                className="btn-brutal bg-paper px-3 py-0.5 font-display text-sm font-bold lowercase"
              >
                {view === 'days' ? `${MONTHS[cursor.month]} ${cursor.year}` : cursor.year}
              </button>
            )}
            <button
              type="button"
              aria-label={`Next ${stepUnit}`}
              onClick={() => step(1)}
              className="btn-brutal bg-white px-2 py-0.5 text-xs"
            >
              ▶
            </button>
          </div>

          {view === 'days' && (
            <div className="grid grid-cols-7 gap-1">
              {WEEKDAYS.map((w) => (
                <span key={w} className="py-1 text-center text-xs font-bold uppercase text-ink/40">
                  {w}
                </span>
              ))}
              {dayCells.map((c) => (
                <button
                  key={c.iso}
                  type="button"
                  aria-label={c.iso}
                  disabled={
                    (min !== undefined && c.iso < min) || (max !== undefined && c.iso > max)
                  }
                  onClick={() => select(c.iso)}
                  className={`h-8 ${cellBase} ${
                    c.iso === value
                      ? cellSelected
                      : `font-medium hover:bg-paper ${c.inMonth ? 'text-ink' : 'text-ink/30'} ${
                          c.iso === todayISO
                            ? 'outline-2 -outline-offset-2 outline-dashed outline-ink/40'
                            : ''
                        }`
                  }`}
                >
                  {c.day}
                </button>
              ))}
            </div>
          )}

          {view === 'months' && (
            <div className="grid grid-cols-3 gap-1">
              {MONTHS.map((m, i) => (
                <button
                  key={m}
                  type="button"
                  disabled={
                    (min !== undefined && toISO(cursor.year, i, lastDay(cursor.year, i)) < min) ||
                    (max !== undefined && toISO(cursor.year, i, 1) > max)
                  }
                  onClick={() => {
                    setCursor({ year: cursor.year, month: i })
                    setView('days')
                  }}
                  className={`h-10 ${cellBase} font-display font-bold lowercase ${
                    selectedYM?.year === cursor.year && selectedYM.month === i
                      ? cellSelected
                      : 'text-ink hover:bg-paper'
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          )}

          {view === 'years' && (
            <div className="grid grid-cols-3 gap-1">
              {Array.from({ length: 12 }, (_, i) => blockStart + i).map((y) => (
                <button
                  key={y}
                  type="button"
                  disabled={
                    (min !== undefined && `${y}-12-31` < min) ||
                    (max !== undefined && `${y}-01-01` > max)
                  }
                  onClick={() => {
                    setCursor({ year: y, month: cursor.month })
                    setView('months')
                  }}
                  className={`h-10 ${cellBase} font-display font-bold lowercase ${
                    selectedYM?.year === y ? cellSelected : 'text-ink hover:bg-paper'
                  }`}
                >
                  {y}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
