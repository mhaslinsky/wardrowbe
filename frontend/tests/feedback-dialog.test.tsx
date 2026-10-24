import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { FeedbackDialog } from '@/components/feedback-dialog'

const submit = vi.hoisted(() => vi.fn().mockResolvedValue({}))
vi.mock('@/lib/hooks/use-outfits', () => ({ useSubmitFeedback: () => ({ mutateAsync: submit, isPending: false }) }))
vi.mock('@/lib/hooks/use-items', () => ({ useItemPages: () => ({ items: [], total: 0, hasMore: false, isLoading: false, isFetching: false }) }))
beforeEach(() => submit.mockClear())

describe('rating an outfit', () => {
  it.each([null, { rating: 4, comment: null }, { rating: null, comment: null, worn_at: null }])('does not rate or log an unworn outfit: %j', async (feedback) => {
    render(<FeedbackDialog outfit={{ id: 'o1', items: [], feedback }} open onClose={() => {}} />)
    expect(screen.queryByText('didYouWear')).not.toBeInTheDocument()
    expect(screen.queryByText('overallRating')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ outfitId: 'o1', feedback: { comment: '' } }))
  })
  it('sends a rating for a worn outfit without wear fields', async () => {
    render(<FeedbackDialog outfit={{ id: 'o2', items: [], feedback: { rating: 4, comment: null, worn_at: '2026-10-24' } }} open onClose={() => {}} />)
    expect(screen.getByText('overallRating')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'submit' }))
    await waitFor(() => expect(submit).toHaveBeenCalledWith({ outfitId: 'o2', feedback: { rating: 4, comment: '' } }))
  })
})
