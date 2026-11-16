import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RejectNoteDialog } from '@/components/reject-note-dialog'

function renderDialog() {
  const onCancel = vi.fn()
  const onConfirm = vi.fn()
  render(<RejectNoteDialog open onCancel={onCancel} onConfirm={onConfirm} />)
  return { onCancel, onConfirm }
}

describe('RejectNoteDialog', () => {
  it('sends the trimmed note', () => {
    const { onConfirm } = renderDialog()

    fireEvent.change(screen.getByPlaceholderText('placeholder'), {
      target: { value: '  no boots with sweats  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }))

    expect(onConfirm).toHaveBeenCalledWith('no boots with sweats')
  })

  it.each(['', '   \n '])('rejects with an empty note when the box holds %j', (value) => {
    const { onConfirm } = renderDialog()

    fireEvent.change(screen.getByPlaceholderText('placeholder'), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: 'confirm' }))

    expect(onConfirm).toHaveBeenCalledWith('')
  })

  it('cancelling does not reject', () => {
    const { onCancel, onConfirm } = renderDialog()

    fireEvent.click(screen.getByRole('button', { name: 'cancel' }))

    expect(onCancel).toHaveBeenCalled()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it.each([{ metaKey: true }, { ctrlKey: true }])('sends the note on %o + Enter', (modifier) => {
    const { onConfirm } = renderDialog()
    const box = screen.getByRole('textbox', { name: 'title' })

    fireEvent.change(box, { target: { value: 'too dressy' } })
    fireEvent.keyDown(box, { key: 'Enter', ...modifier })

    expect(onConfirm).toHaveBeenCalledWith('too dressy')
  })

  it('plain Enter adds a line instead of sending', () => {
    const { onConfirm } = renderDialog()

    fireEvent.keyDown(screen.getByRole('textbox', { name: 'title' }), { key: 'Enter' })

    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('caps the note at the length the server accepts', () => {
    renderDialog()

    expect(screen.getByPlaceholderText('placeholder').getAttribute('maxlength')).toBe('1000')
  })
})
