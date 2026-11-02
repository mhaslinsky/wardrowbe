import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/lib/api'
import {
  useRemoveBackground,
  useReplaceItemImage,
  useRestoreOriginal,
  useRotateImage,
  useItem,
  useItems,
  useSetPrimaryImage,
} from '@/lib/hooks/use-items'
import { queryKeys } from '@/lib/hooks/query-keys'
import type { Item, OutfitItem } from '@/lib/types'

vi.mock('next/image', () => ({
  default: ({ src, alt }: { src: string; alt: string }) => <img src={src} alt={alt} />,
}))
vi.mock('@/lib/hooks/use-family', () => ({
  useFamily: () => ({ data: null }),
  useCurrentFamilyMember: () => ({ member: null }),
}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  api: { get: vi.fn(), post: vi.fn().mockResolvedValue({}), delete: vi.fn().mockResolvedValue(undefined) },
  getAccessToken: vi.fn(() => null),
}))

const PRIMARY_IMAGE_KEYS = [['items'], ['item', 'item-1'], ['outfits'], ['calendarOutfits']]

function setup() {
  const client = new QueryClient()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  return { invalidate, wrapper }
}

type Wrapper = ReturnType<typeof setup>['wrapper']

async function mutate<V>(
  useHook: () => { mutateAsync: (variables: V) => Promise<unknown> },
  wrapper: Wrapper,
  variables: V
) {
  const { result } = renderHook(useHook, { wrapper })
  await act(async () => {
    await result.current.mutateAsync(variables)
  })
}

function invalidatedKeys(invalidate: ReturnType<typeof setup>['invalidate']) {
  return invalidate.mock.calls.map(([filters]) => filters?.queryKey)
}

afterEach(() => {
  cleanup()
  vi.mocked(api.post).mockClear()
  vi.restoreAllMocks()
})

it('renders the rotated URL in item and wardrobe queries before refetch finishes', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
  const previous = { id: 'item-1', image_url: '/old.jpg', medium_url: '/old-medium.jpg' } as Item
  const rotated = { ...previous, image_url: '/rotated.jpg', medium_url: '/rotated-medium.jpg' }
  client.setQueryData(queryKeys.item(previous.id), previous)
  client.setQueryData(queryKeys.items.list({}, 1, 24), { items: [previous], total: 1 })
  vi.mocked(api.get).mockImplementation(() => new Promise(() => {}))
  vi.mocked(api.post).mockResolvedValueOnce(rotated)
  let finished!: () => void
  const completed = new Promise<void>((resolve) => { finished = resolve })

  function Photos() {
    const { data: item } = useItem(previous.id)
    const { data: wardrobe } = useItems({}, 1, 24)
    const mutation = useRotateImage()
    return <>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img alt="Item" src={item?.medium_url || ''} />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img alt="Wardrobe" src={wardrobe?.items[0].image_url || ''} />
      <button onClick={async () => {
        await mutation.mutateAsync({ id: previous.id, direction: 'cw' })
        finished()
      }}>Rotate</button>
    </>
  }

  render(<QueryClientProvider client={client}><Photos /></QueryClientProvider>)
  expect(screen.getByAltText('Item')).toHaveAttribute('src', '/old-medium.jpg')
  expect(screen.getByAltText('Wardrobe')).toHaveAttribute('src', '/old.jpg')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Rotate' }))
    await completed
  })
  expect(client.getQueryData(queryKeys.item(previous.id))).toEqual(rotated)
  await waitFor(() => {
    expect(screen.getByAltText('Item')).toHaveAttribute('src', rotated.medium_url)
    expect(screen.getByAltText('Wardrobe')).toHaveAttribute('src', rotated.image_url)
  })
  client.clear()
})

describe('mutations that change an item primary image', () => {
  const cases: Array<[string, (wrapper: Wrapper) => Promise<unknown>]> = [
    [
      'set primary',
      (wrapper) =>
        mutate(() => useSetPrimaryImage(), wrapper, { itemId: 'item-1', imageId: 'img-2' }),
    ],
    ['rotate', (wrapper) => mutate(() => useRotateImage(), wrapper, { id: 'item-1', direction: 'cw' })],
    ['remove background', (wrapper) => mutate(() => useRemoveBackground(), wrapper, { id: 'item-1' })],
    ['restore original', (wrapper) => mutate(() => useRestoreOriginal(), wrapper, 'item-1')],
    [
      'replace image',
      (wrapper) => {
        vi.spyOn(global, 'fetch').mockResolvedValue(new Response('{}', { status: 200 }))
        return mutate(() => useReplaceItemImage(), wrapper, {
          itemId: 'item-1',
          file: new File(['x'], 'a.jpg'),
        })
      },
    ],
  ]

  it.each(cases)('%s refreshes item and outfit caches', async (_name, run) => {
    const { invalidate, wrapper } = setup()
    await run(wrapper)
    expect(invalidatedKeys(invalidate)).toEqual(expect.arrayContaining(PRIMARY_IMAGE_KEYS))
  })
})

it('renders the rotated URL in the real outfit dialog while the outfit snapshot stays unchanged', async () => {
  const { OutfitPreviewDialog } = await import('@/components/outfit-preview-dialog')
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
  const previous: OutfitItem = {
    id: 'item-1', name: 'Shirt', type: 'shirt', image_url: '/old.jpg',
    image_path: 'old.jpg', thumbnail_path: null, thumbnail_url: null,
    subtype: null, primary_color: null, colors: [], layer_type: null,
    position: 0, needs_wash: false,
  }
  const rotated = { ...previous, image_url: '/rotated.jpg' }
  const outfit = {
    id: 'outfit-1', items: [previous], occasion: 'casual', scheduled_for: null,
    reasoning: null, style_notes: null, highlights: null, family_ratings: null,
    family_rating_average: null, family_rating_count: null,
  }
  vi.mocked(api.get).mockImplementation(() => new Promise(() => {}))
  vi.mocked(api.post).mockResolvedValueOnce(rotated)
  render(<QueryClientProvider client={client}>
    <OutfitPreviewDialog outfit={outfit} open onClose={() => {}} />
  </QueryClientProvider>)
  expect(screen.getByAltText('Shirt')).toHaveAttribute('src', '/old.jpg')
  fireEvent.click(screen.getByTitle('rotateRight'))
  await waitFor(() => expect(screen.getByAltText('Shirt')).toHaveAttribute('src', '/rotated.jpg'))
  expect(outfit.items[0].image_url).toBe('/old.jpg')
  client.clear()
})
