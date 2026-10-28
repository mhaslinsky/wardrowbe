import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import { OutfitCard } from '@/components/outfits/outfit-card'
import type { Outfit } from '@/lib/hooks/use-outfits'
import OutfitDetailPage from '@/app/dashboard/outfits/[id]/page'

vi.mock('@/components/outfit-photo', () => ({ OutfitPhoto: () => null }))

vi.unmock('next-intl')

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ id: 'o1' }),
}))

const outfit = vi.hoisted(() => ({
  replaces_outfit_id: null as string | null,
  id: 'o1',
  scheduled_for: '2026-10-05',
  source: 'on_demand',
  occasion: 'casual',
  name: 'Look' as string | null,
  items: [
    { id: 'shirt-1', name: 'Blue shirt', type: 'top', needs_wash: true },
    { id: 'trousers-2', name: 'Black trousers', type: 'bottom', needs_wash: false },
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

describe('outfit wash detail', () => {
  it('badges only dirty items', () => {
    const en = Object.fromEntries(['outfits', 'common', 'history', 'constants'].map(namespace => [namespace, messages('en', namespace)]))
    render(<NextIntlClientProvider locale="en" messages={en}><OutfitDetailPage /></NextIntlClientProvider>)
    expect(screen.getAllByText('In the wash')).toHaveLength(1)
  })
})

describe('outfit wash card', () => {
  it('counts dirty pieces and omits the line when clean', () => {
    const en = Object.fromEntries(['outfits', 'common', 'history', 'constants'].map(namespace => [namespace, messages('en', namespace)]))
    const { rerender } = render(<NextIntlClientProvider locale="en" messages={en}><OutfitCard outfit={outfit as unknown as Outfit} /></NextIntlClientProvider>)
    expect(screen.getByText('1 piece in the wash')).toBeInTheDocument()
    expect(screen.getAllByText('In the wash')).toHaveLength(1)
    const clean = { ...outfit, items: outfit.items.map(item => ({ ...item, needs_wash: false })) }
    rerender(<NextIntlClientProvider locale="en" messages={en}><OutfitCard outfit={clean as unknown as Outfit} /></NextIntlClientProvider>)
    expect(screen.queryByText('In the wash')).not.toBeInTheDocument()
    expect(screen.queryByText('1 piece in the wash')).not.toBeInTheDocument()
  })
})

vi.mock('@/components/outfit-preview', () => ({ OutfitPreview: ({ children }: { children: React.ReactNode }) => children }))
