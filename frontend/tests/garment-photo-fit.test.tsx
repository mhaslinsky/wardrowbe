import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ItemDetailDialog } from '@/components/item-detail-dialog'
import WardrobePage from '@/app/dashboard/wardrobe/page'
import type { Item } from '@/lib/types'

const item = vi.hoisted(() => ({
  id: 'garment', name: 'Whole garment', type: 't-shirt', status: 'ready',
  image_url: '/original?signature=test', medium_url: '/medium', thumbnail_url: '/thumb',
  image_path: 'original.jpg', favorite: false, tags: {}, additional_images: [],
}))
vi.mock('next/image', () => ({ default: ({ fill, priority, ...props }: any) => <img {...props} /> }))
vi.mock('@/components/add-item-dialog', () => ({ AddItemDialog: () => null }))
vi.mock('@/components/analysis-queue-panel', () => ({ AnalysisQueuePanel: () => null }))
vi.mock('@/components/bulk-action-toolbar', () => ({ BulkActionToolbar: () => null }))
vi.mock('@/components/generate-pairings-dialog', () => ({ GeneratePairingsDialog: () => null }))
vi.mock('@/lib/hooks/use-features', () => ({ useFeatures: () => ({ data: {} }) }))
vi.mock('@/lib/hooks/use-user', () => ({ useUserTimezone: () => 'America/New_York' }))
vi.mock('@/lib/hooks/use-items', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/hooks/use-items')>()
  const hooks = Object.fromEntries(Object.keys(original).filter((key) => key.startsWith('use')).map((key) => [
    key, () => ({ data: undefined, isPending: false, mutateAsync: vi.fn() }),
  ]))
  return {
    ...original, ...hooks,
    useItems: () => ({ data: { items: [item], total: 1, total_pages: 1 }, isLoading: false }),
    useItemTypes: () => ({ data: [] }),
  }
})

afterEach(cleanup)

function mount(children: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>)
}

describe('whole garment photos', () => {
  it('renders the detail photo uncropped at original resolution', () => {
    mount(<ItemDetailDialog item={item as unknown as Item} open onOpenChange={vi.fn()} />)
    const photo = screen.getByRole('img', { name: 'Whole garment' })
    expect(photo).toHaveClass('object-contain')
    expect(photo.getAttribute('src')).toMatch(/^\/original\?signature=test/)
  })
  it('renders the wardrobe grid photo uncropped at medium resolution', () => {
    mount(<WardrobePage />)
    const photo = screen.getByRole('img', { name: 'Whole garment' })
    expect(photo).toHaveClass('object-contain')
    expect(photo).toHaveAttribute('src', '/medium')
  })
})
