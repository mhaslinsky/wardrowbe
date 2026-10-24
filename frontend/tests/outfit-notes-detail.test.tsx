import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import OutfitDetailPage from '@/app/dashboard/outfits/[id]/page'

vi.unmock('next-intl')

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ id: 'o1' }),
}))

const outfit = vi.hoisted(() => ({
  replaces_outfit_id: null as string | null,
  id: 'o1',
  scheduled_for: '2026-10-05' as string | null,
  source: 'on_demand',
  occasion: 'casual',
  name: 'Look' as string | null,
  items: [
    { id: 'shirt-1', name: 'Blue shirt', type: 'top' },
    { id: 'trousers-2', name: 'Black trousers', type: 'bottom' },
  ],
  feedback: null,
}))

vi.mock('@/lib/hooks/use-outfits', () => ({
  useOutfit: () => ({ data: outfit, isLoading: false }),
  useOutfits: () => ({ data: undefined }),
  useDeleteOutfit: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-studio', () => ({
  useWearToday: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-05' }))
vi.mock('@/components/shared/lineage-card', () => ({ LineageCard: () => null }))
vi.mock('@/components/shared/clone-to-lookbook-dialog', () => ({
  CloneToLookbookDialog: () => null,
}))

function messages(locale: string, namespace: string) {
  return JSON.parse(
    readFileSync(resolve(__dirname, '..', 'messages', locale, `${namespace}.json`), 'utf8'),
  )
}

vi.mock('@/components/feedback-dialog', () => ({
  FeedbackDialog: ({ open, noteOnly, outfit: target }: { open: boolean; noteOnly: boolean; outfit: { id: string } }) =>
    open ? <div data-testid="note-dialog">{`${target.id}:${noteOnly}`}</div> : null,
}))

it.each([null, '2026-10-05'])('opens note feedback for outfit dated %s', (scheduled) => {
  outfit.scheduled_for = scheduled
  const en = Object.fromEntries(['outfits', 'common', 'history', 'constants'].map(namespace => [namespace, messages('en', namespace)]))
  render(<NextIntlClientProvider locale="en" messages={en}><OutfitDetailPage /></NextIntlClientProvider>)
  fireEvent.click(screen.getByText('Add note', { selector: 'button' }))
  expect(screen.getByTestId('note-dialog')).toHaveTextContent('o1:true')
})
