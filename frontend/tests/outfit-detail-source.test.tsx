import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
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
  scheduled_for: '2026-10-05' as string | null,
  unowned_item_ids: [] as string[],
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
const wearRequest = vi.hoisted(() => vi.fn().mockResolvedValue({ id: 'worn' }))
vi.mock('@/lib/hooks/use-studio', () => ({
  useWearToday: () => ({ mutateAsync: wearRequest, isPending: false }),
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

describe('outfit detail source label', () => {
  it('links each item to its wardrobe detail dialog', () => {
    const en = {
      outfits: messages('en', 'outfits'),
      common: messages('en', 'common'),
      history: messages('en', 'history'),
      constants: messages('en', 'constants'),
    }
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <OutfitDetailPage />
      </NextIntlClientProvider>,
    )
    for (const item of outfit.items) {
      expect(screen.getByText(item.name, { selector: 'a p' }).closest('a')).toHaveAttribute(
        'href', `/dashboard/wardrobe?item=${item.id}`,
      )
    }
  })

  it('shows the translated source on the German locale', () => {
    const de = {
      outfits: messages('de', 'outfits'),
      common: messages('de', 'common'),
      history: messages('de', 'history'),
      constants: messages('de', 'constants'),
    }
    render(
      <NextIntlClientProvider locale="de" messages={de} onError={() => {}}>
        <OutfitDetailPage />
      </NextIntlClientProvider>,
    )
    expect(screen.getByText(de.constants.outfitSources.onDemand)).toBeInTheDocument()
    expect(screen.queryByText('on demand')).not.toBeInTheDocument()
  })

  it('shows the translated wore-instead title for an unnamed replacement', () => {
    outfit.name = null
    outfit.replaces_outfit_id = 'o0'
    const de = {
      outfits: messages('de', 'outfits'),
      common: messages('de', 'common'),
      history: messages('de', 'history'),
      constants: messages('de', 'constants'),
    }
    render(
      <NextIntlClientProvider locale="de" messages={de} onError={() => {}}>
        <OutfitDetailPage />
      </NextIntlClientProvider>,
    )
    expect(screen.getByText(`${de.constants.occasions.casual} (stattdessen getragen)`, { selector: 'h1' })).toBeInTheDocument()
    outfit.name = 'Look'
    outfit.replaces_outfit_id = null
  })
})

vi.mock('@/components/outfit-preview', () => ({ OutfitPreview: ({ children }: { children: React.ReactNode }) => children }))

function purchasePage() {
  const en = Object.fromEntries(['outfits', 'common', 'history', 'constants'].map(namespace => [namespace, messages('en', namespace)]))
  return <NextIntlClientProvider locale="en" messages={en}><OutfitDetailPage /></NextIntlClientProvider>
}

it('purchase membership disables both wear buttons', () => {
  outfit.scheduled_for = null
  try {
    const view = render(purchasePage())
    fireEvent.click(screen.getByRole('button', { name: 'Wear on…' }))
    outfit.unowned_item_ids = ['shirt-1']
    view.rerender(purchasePage())
    expect(screen.getByRole('button', { name: 'Wear on…', hidden: true })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Log wear' })).toBeDisabled()
  } finally {
    outfit.scheduled_for = '2026-10-05'
    outfit.unowned_item_ids = []
  }
})

it('purchase membership prevents the wear handler from sending a request', () => {
  outfit.scheduled_for = null
  wearRequest.mockClear()
  try {
    render(purchasePage())
    fireEvent.click(screen.getByRole('button', { name: 'Wear on…' }))
    const submit = screen.getByRole('button', { name: 'Log wear' })
    expect(submit).toBeEnabled()
    outfit.unowned_item_ids = ['shirt-1']
    fireEvent.click(submit)
    expect(wearRequest).not.toHaveBeenCalled()
  } finally {
    outfit.scheduled_for = '2026-10-05'
    outfit.unowned_item_ids = []
  }
})
