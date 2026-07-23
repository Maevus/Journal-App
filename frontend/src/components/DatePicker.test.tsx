import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { DatePicker } from './DatePicker'

const noop = () => {}

const openPicker = (name = 'Pick') => fireEvent.click(screen.getByRole('button', { name }))

describe('DatePicker', () => {
  it('shows a placeholder, then the value euro-style', () => {
    const { rerender } = render(<DatePicker label="Pick" value={null} onChange={noop} />)
    expect(screen.getByRole('button', { name: 'Pick' })).toHaveTextContent('dd-mm-yyyy')

    rerender(<DatePicker label="Pick" value="2026-03-05" onChange={noop} />)
    expect(screen.getByRole('button', { name: 'Pick' })).toHaveTextContent('05-03-2026')
  })

  it('opens on the value month; selecting a day emits ISO and closes', () => {
    const onChange = vi.fn()
    render(<DatePicker label="Pick" value="2026-03-15" onChange={onChange} />)
    openPicker()

    expect(screen.getByRole('button', { name: 'mar 2026' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '2026-03-04' }))
    expect(onChange).toHaveBeenCalledWith('2026-03-04')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('zooms out to months and back down to days', () => {
    render(<DatePicker label="Pick" value="2026-03-15" onChange={noop} />)
    openPicker()

    fireEvent.click(screen.getByRole('button', { name: 'mar 2026' }))
    // Month grid replaces the day grid…
    expect(screen.queryByRole('button', { name: '2026-03-15' })).not.toBeInTheDocument()
    // …and picking a month returns to the day view (the native picker's dead-end).
    fireEvent.click(screen.getByRole('button', { name: 'jun' }))
    expect(screen.getByRole('button', { name: 'jun 2026' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '2026-06-15' })).toBeInTheDocument()
  })

  it('zooms out to years and back down through months', () => {
    render(<DatePicker label="Pick" value="2026-03-15" onChange={noop} />)
    openPicker()

    fireEvent.click(screen.getByRole('button', { name: 'mar 2026' }))
    fireEvent.click(screen.getByRole('button', { name: '2026' }))
    expect(screen.getByText('2016 – 2027')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '2019' }))
    fireEvent.click(screen.getByRole('button', { name: 'feb' }))
    expect(screen.getByRole('button', { name: 'feb 2019' })).toBeInTheDocument()
  })

  it('steps months with the arrows', () => {
    render(<DatePicker label="Pick" value="2026-03-15" onChange={noop} />)
    openPicker()

    fireEvent.click(screen.getByRole('button', { name: 'Previous month' }))
    expect(screen.getByRole('button', { name: 'feb 2026' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next month' }))
    expect(screen.getByRole('button', { name: 'mar 2026' })).toBeInTheDocument()
  })

  it('disables days outside min/max', () => {
    render(
      <DatePicker
        label="Pick"
        value="2026-03-15"
        min="2026-03-10"
        max="2026-03-20"
        onChange={noop}
      />,
    )
    openPicker()

    expect(screen.getByRole('button', { name: '2026-03-05' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '2026-03-25' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '2026-03-10' })).toBeEnabled()
  })

  it('closes on Escape and on outside mousedown', () => {
    render(<DatePicker label="Pick" value="2026-03-15" onChange={noop} />)
    openPicker()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pick' })).toHaveFocus()

    openPicker()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
