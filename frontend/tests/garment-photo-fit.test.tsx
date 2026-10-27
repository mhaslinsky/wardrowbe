import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ItemDetailDialog } from '@/components/item-detail-dialog'
import WardrobePage from '@/app/dashboard/wardrobe/page'
import { OutfitCard } from '@/components/outfits/outfit-card'
import { OutfitHistoryCard } from '@/components/outfit-history-card'
import { OutfitPreviewDialog } from '@/components/outfit-preview-dialog'
import OutfitDetailPage from '@/app/dashboard/outfits/[id]/page'
import SuggestPage from '@/app/dashboard/suggest/page'
import { CanvasPanel } from '@/components/studio/canvas-panel'
import { ItemPicker } from '@/components/shared/item-picker'
import type { Item, Outfit } from '@/lib/types'

const item = vi.hoisted(() => ({
  id: 'garment', name: 'Whole garment', type: 't-shirt', status: 'ready',
  image_url: '/original?signature=test', medium_url: '/medium', thumbnail_url: '/thumb',
  image_path: 'original.jpg', favorite: false, tags: {}, additional_images: [],
}))
const outfit = vi.hoisted(() => ({
  id: 'outfit', occasion: 'casual', source: 'manual', status: 'accepted',
  name: 'Outfit', scheduled_for: null, items: [] as any[], feedback: null,
}))
const post = vi.hoisted(() => vi.fn())
const itemsData = vi.hoisted(() => ({ items: [] as any[], total: 1, total_pages: 1 }))
vi.mock('next/image', () => ({ default: ({ fill, priority, ...props }: any) => <img {...props} /> }))
vi.mock('@/components/add-item-dialog', () => ({ AddItemDialog: () => null }))
vi.mock('@/components/analysis-queue-panel', () => ({ AnalysisQueuePanel: () => null }))
vi.mock('@/components/bulk-action-toolbar', () => ({ BulkActionToolbar: () => null }))
vi.mock('@/components/generate-pairings-dialog', () => ({ GeneratePairingsDialog: () => null }))
vi.mock('@/lib/hooks/use-features', () => ({ useFeatures: () => ({ data: {} }) }))
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useParams: () => ({ id: 'outfit' }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('@/lib/hooks/use-user', () => ({
  useUserProfile: () => ({ data: { timezone: 'America/New_York' } }),
  useUserTimezone: () => 'America/New_York',
  useUserToday: () => () => '2026-10-23',
}))
vi.mock('@/lib/hooks/use-outfits', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/hooks/use-outfits')>()
  const hooks = Object.fromEntries(Object.keys(original).filter((key) => key.startsWith('use')).map((key) => [
    key, () => ({ data: undefined, isPending: false, mutateAsync: vi.fn() }),
  ]))
  return { ...original, ...hooks, useOutfit: () => ({ data: outfit, isLoading: false }) }
})
vi.mock('@/lib/hooks/use-studio', () => ({ useWearToday: () => ({ mutateAsync: vi.fn() }) }))
vi.mock('@/lib/hooks/use-family', () => ({
  useFamily: () => ({ data: undefined }),
  useCurrentFamilyMember: () => ({ member: undefined }),
}))
vi.mock('@/lib/hooks/use-weather', () => ({ useWeather: () => ({ data: undefined, isLoading: false }) }))
vi.mock('@/lib/hooks/use-preferences', () => ({
  usePreferences: () => ({ data: { default_occasion: 'casual' } }),
}))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/api')>(),
  api: { post },
}))
vi.mock('@/components/shared/lineage-card', () => ({ LineageCard: () => null }))
vi.mock('@/components/shared/clone-to-lookbook-dialog', () => ({ CloneToLookbookDialog: () => null }))
vi.mock('@/lib/hooks/use-items', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/hooks/use-items')>()
  const hooks = Object.fromEntries(Object.keys(original).filter((key) => key.startsWith('use')).map((key) => [
    key, () => ({ data: undefined, isPending: false, mutateAsync: vi.fn() }),
  ]))
  return {
    ...original, ...hooks,
    useItems: () => ({ data: itemsData, isLoading: false }),
    useItemTypes: () => ({ data: [] }),
  }
})

afterEach(cleanup)
beforeEach(() => {
  item.medium_url = '/medium'
  outfit.items = [item]
  itemsData.items = [item]
  post.mockResolvedValue([outfit])
})

function mount(children: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>)
}

describe('whole garment photos', () => {
  it('renders the detail photo uncropped at original resolution', () => {
    mount(<ItemDetailDialog item={item as unknown as Item} open onOpenChange={vi.fn()} />)
    const photo = screen.getByRole('img', { name: 'Whole garment' })
    expect(photo).toHaveClass('object-contain')
    expect(photo).not.toHaveClass('object-cover')
    expect(photo.getAttribute('src')).toMatch(/^\/original\?signature=test/)
  })
  it('renders the wardrobe grid photo uncropped at medium resolution', () => {
    mount(<WardrobePage />)
    const photo = screen.getByRole('img', { name: 'Whole garment' })
    expect(photo).toHaveClass('object-contain')
    expect(photo).not.toHaveClass('object-cover')
    expect(photo).toHaveAttribute('src', '/medium')
  })
  const views = [
    { name: 'outfit card', src: '/medium', render: () => <OutfitCard outfit={outfit as unknown as Outfit} /> },
    { name: 'outfit history', src: '/thumb', render: () => <OutfitHistoryCard outfit={outfit as unknown as Outfit} onFeedback={vi.fn()} /> },
    { name: 'outfit preview', src: '/original?signature=test', render: () => <OutfitPreviewDialog outfit={outfit as unknown as Outfit} open onClose={vi.fn()} /> },
    { name: 'outfit detail', src: '/medium', render: () => <OutfitDetailPage /> },
    { name: 'studio canvas', src: '/thumb', render: () => <CanvasPanel items={[item]} onRemove={vi.fn()} /> },
    { name: 'item picker', src: '/medium', render: () => <ItemPicker selectedIds={new Set()} onToggle={vi.fn()} /> },
  ]
  it.each(views)('renders $name without cropping at the intended size', ({ render: view, src }) => {
    mount(view())
    const photo = screen.getAllByRole('img', { name: 'Whole garment' })[0]
    expect(photo).toHaveClass('object-contain')
    expect(photo).not.toHaveClass('object-cover')
    expect(photo.getAttribute('src')).toMatch(new RegExp(`^${src.replace('?', '\\?')}`))
  })
  it.each(views.filter((view) => view.src === '/medium'))('uses a thumbnail in $name when medium is missing', ({ render: view }) => {
    item.medium_url = null as unknown as string
    mount(view())
    const photo = screen.getByRole('img', { name: 'Whole garment' })
    expect(photo).toHaveAttribute('src', '/thumb')
  })
  it('renders suggest result tiles without cropping at medium resolution', async () => {
    mount(<SuggestPage />)
    fireEvent.click(screen.getByRole('button', { name: 'getSuggestion' }))
    const photo = await screen.findByRole('img', { name: 'Whole garment' })
    expect(photo).toHaveClass('object-contain')
    expect(photo).not.toHaveClass('object-cover')
    expect(photo).toHaveAttribute('src', '/medium')
    expect(photo).not.toHaveClass('group-hover:scale-105')
  })
})
