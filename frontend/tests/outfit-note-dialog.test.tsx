import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FeedbackDialog } from '@/components/feedback-dialog'

const submit = vi.hoisted(() => vi.fn().mockResolvedValue({}))
vi.mock('@/lib/hooks/use-outfits', () => ({
  useSubmitFeedback: () => ({ mutateAsync: submit, isPending: false }),
}))
vi.mock('@/lib/hooks/use-items', () => ({
  useItemPages: () => ({ items: [], total: 0, hasMore: false, isLoading: false, isFetching: false }),
}))

describe('note-only feedback', () => {
  it('saves a trimmed note without changing wear or rating', async () => {
    const close = vi.fn()
    render(<FeedbackDialog outfit={{ id: 'outfit-1', items: [], feedback: { rating: 4, comment: 'Old note' } }} open noteOnly onClose={close} />)
    expect(screen.queryByText('didYouWear')).not.toBeInTheDocument()
    expect(screen.queryByText('overallRating')).not.toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('commentsPlaceholder'), { target: { value: '  You forgot a shirt  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await waitFor(() => expect(close).toHaveBeenCalledOnce())
    expect(submit).toHaveBeenCalledWith({ outfitId: 'outfit-1', feedback: { comment: 'You forgot a shirt' } })
  })

  it('can clear an existing note', async () => {
    render(<FeedbackDialog outfit={{ id: 'outfit-2', items: [], feedback: { rating: null, comment: 'Old note' } }} open noteOnly onClose={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText('commentsPlaceholder'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ outfitId: 'outfit-2', feedback: { comment: '' } }))
  })
})
