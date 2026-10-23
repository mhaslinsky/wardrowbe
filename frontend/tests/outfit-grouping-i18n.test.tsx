import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider, createTranslator } from 'next-intl'
import { describe, expect, it, vi } from 'vitest'
import { OutfitCard } from '@/components/outfits/outfit-card'
import type { Outfit } from '@/lib/hooks/use-outfits'
import en from '@/messages/en/outfits.json'

vi.unmock('next-intl')
vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-23' }))
vi.mock('@/lib/hooks/use-outfit-title', () => ({ useOutfitTitle: () => () => 'Saved look' }))
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => () => 'Shirt', useOccasionLabel: () => () => 'Casual' }))

function card(wear_count: number) {
  return render(<NextIntlClientProvider locale="en" messages={{ outfits: en }}>
    <OutfitCard outfit={{ id: 'look', source: 'manual', occasion: 'casual', items: [], wear_count } as unknown as Outfit} />
  </NextIntlClientProvider>)
}

describe('grouped wear counts', () => {
  it('uses the singular form for one wear', () => {
    card(1)
    expect(screen.getByText('Worn 1 time')).toBeInTheDocument()
  })
  it('hides zero wears', () => {
    card(0)
    expect(screen.queryByText(/Worn 0/)).not.toBeInTheDocument()
  })
  it('matches the existing plural forms in every locale', () => {
    const catalogs = import.meta.glob('../messages/*/outfits.json', { eager: true, import: 'default' })
    for (const [path, catalog] of Object.entries(catalogs)) {
      const locale = path.split('/')[2]
      const messages = catalog as typeof en
      const translate = createTranslator({ locale, messages })
      expect(messages.cards.wearCount).toBe(messages.detail.wornCount)
      expect(translate('cards.wearCount', { count: 2 })).not.toContain('{count')
    }
  })
})
