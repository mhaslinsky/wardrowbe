import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { act, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { WearActions } from '@/components/day-outfits'
import { useItemPages } from '@/lib/hooks/use-items'
import { queryKeys } from '@/lib/hooks/query-keys'
import type { Item, Outfit } from '@/lib/types'
import { sessionMock, translationMock } from './setup'

const wear = vi.hoisted(() => vi.fn())
vi.mock('@/lib/hooks/use-day-outfits', () => ({ useLogDayWear: () => ({ mutateAsync: wear, isPending: false }) }))
vi.mock('@/lib/hooks/use-user', () => ({
  useUserToday: () => () => '2026-10-24',
  useUserProfile: () => ({ data: { timezone: 'America/New_York' } }),
}))

const makeItem = (id: string, name: string, type = 'shirt', extra = {}) => ({
  id, name, type, status: 'ready', needs_wash: false, is_archived: false, ...extra,
} as Item)
const currentShoe = makeItem('stan-smiths', 'Navy Stan Smiths', 'sneakers')
const boots = makeItem('vessi', 'Charcoal Vessi Chelsea boots', 'boots')
const grayShoe = makeItem('gray-adidas', 'Gray Adidas sneaker', 'sneakers')
const outfit = { id: 'suggestion', name: 'Today', status: 'pending', items: [currentShoe] } as unknown as Outfit
const wardrobe = [currentShoe, boots, ...Array.from({ length: 170 }, (_, index) => makeItem(`shirt-${index}`, `Shirt ${index}`)), grayShoe]
const largeWardrobe = [currentShoe, boots, ...Array.from({ length: 270 }, (_, index) => makeItem(`shoe-${index}`, `Shoe ${index}`, 'sneakers')), grayShoe]
const filters = { needs_wash: false, is_archived: false }
let client: QueryClient
let requests: number[]

function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

function servePage(page: number, items = wardrobe) {
  const start = (page - 1) * 100
  return Response.json({ items: items.slice(start, start + 100), total: items.length, page, page_size: 100, has_more: start + 100 < items.length })
}

function stubItems(secondPage: () => Response | Promise<Response> = () => servePage(2)) {
  vi.mocked(fetch).mockImplementation(async (input, options) => {
    const url = new URL(String(input), 'http://localhost')
    expect(url.pathname).toBe('/api/v1/items')
    expect(options?.method).toBe('GET')
    expect(url.searchParams.get('needs_wash')).toBe('false')
    expect(url.searchParams.get('is_archived')).toBe('false')
    expect(url.searchParams.get('page_size')).toBe('100')
    const page = Number(url.searchParams.get('page'))
    requests.push(page)
    if (page === 1) return servePage(1)
    if (page === 2) return secondPage()
    throw new Error(`Unexpected page ${page}`)
  })
}

function openPicker() {
  render(<WearActions outfit={outfit} date="2026-10-24" />, { wrapper })
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  return within(screen.getByRole('dialog'))
}

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  requests = []
  vi.mocked(fetch).mockReset()
  wear.mockReset()
  translationMock.includeNamespace = false
})

afterEach(() => {
  client.clear()
  sessionMock.status = 'unauthenticated'
  translationMock.includeNamespace = false
})

it('loads all 173 items and offers the page-2 shoe without another click (smoke)', async () => {
  stubItems()
  const picker = openPicker()
  expect(await picker.findByRole('option', { name: grayShoe.name! })).toBeVisible()
  expect(requests).toEqual([1, 2])
  expect(picker.queryByRole('button', { name: 'loadMore' })).not.toBeInTheDocument()
  expect(picker.queryByRole('status')).not.toBeInTheDocument()
  const labels = picker.getAllByRole('option').map((option) => option.textContent)
  process.stdout.write(`SMOKE 173 items, pages 100 + 73, footwear options: ${JSON.stringify(labels)}\n`)
})

it('keeps loading visible and confirmation disabled until the last page arrives', async () => {
  let finishPage!: (response: Response) => void
  stubItems(() => new Promise((resolve) => { finishPage = resolve }))
  const picker = openPicker()
  await waitFor(() => expect(requests).toEqual([1, 2]))
  fireEvent.change(picker.getByRole('combobox'), { target: { value: boots.id } })
  expect(picker.getByRole('status')).toHaveTextContent('loading')
  expect(picker.getByRole('button', { name: 'wore' })).toBeDisabled()
  await act(async () => finishPage(servePage(2)))
  await waitFor(() => expect(picker.queryByRole('status')).not.toBeInTheDocument())
  expect(picker.getByRole('button', { name: 'wore' })).toBeEnabled()
})

it.each(['HTTP error', 'malformed JSON', 'network failure'])('reports a second-page %s and blocks confirmation without advancing again', async (failure) => {
  stubItems(() => {
    if (failure === 'network failure') return Promise.reject(new Error('Disconnected'))
    if (failure === 'malformed JSON') return new Response('invalid json')
    return Response.json({ detail: 'Page unavailable' }, { status: 503 })
  })
  const picker = openPicker()
  expect(await picker.findByRole('alert')).toHaveTextContent('unexpectedError')
  fireEvent.change(picker.getByRole('combobox'), { target: { value: boots.id } })
  expect(picker.getByRole('button', { name: 'wore' })).toBeDisabled()
  expect(requests).toEqual([1, 2])
  expect(wear).not.toHaveBeenCalled()
})

it('sorts display names after the current piece, including unnamed items', async () => {
  const choices = [boots, makeItem('zulu', 'Zulu shoe', 'sneakers'), makeItem('unnamed', '', 'boots'), grayShoe]
  vi.mocked(fetch).mockResolvedValue(servePage(1, choices))
  const picker = openPicker()
  await picker.findByRole('option', { name: grayShoe.name! })
  expect(picker.getAllByRole('option').map((option) => option.textContent)).toEqual([
    currentShoe.name, 'boots', boots.name, grayShoe.name, 'Zulu shoe',
  ])
})

it('exposes errors from any page even when a later page succeeds', async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const page = Number(new URL(String(input), 'http://localhost').searchParams.get('page'))
    return page === 1 ? Response.json({ detail: 'Unavailable' }, { status: 500 }) : servePage(2)
  })
  const { result } = renderHook(() => useItemPages({ needs_wash: false, is_archived: false }, 2, 100), { wrapper })
  await waitFor(() => expect(result.current.isFetching).toBe(false))
  expect(result.current.isError).toBe(true)
  expect(result.current.items).toContainEqual(grayShoe)
})

it('handles an empty wardrobe', async () => {
  vi.mocked(fetch).mockResolvedValue(servePage(1, []))
  const picker = openPicker()
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
  await waitFor(() => expect(picker.queryByRole('status')).not.toBeInTheDocument())
  expect(picker.getAllByRole('option')).toHaveLength(1)
  expect(picker.getByRole('button', { name: 'wore' })).toBeDisabled()
})

it('resets the selection when the picker reopens', async () => {
  vi.mocked(fetch).mockResolvedValue(servePage(1, [boots]))
  const picker = openPicker()
  await picker.findByRole('option', { name: boots.name! })
  fireEvent.change(picker.getByRole('combobox'), { target: { value: boots.id } })
  expect(picker.getByRole('button', { name: 'wore' })).toBeEnabled()
  fireEvent.click(picker.getByRole('button', { name: 'Close' }))
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  expect(within(screen.getByRole('dialog')).getByRole('combobox')).toHaveValue(currentShoe.id)
  expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'wore' })).toBeDisabled()
})

it('reopens all three cached pages without stalling and enables a chosen swap', async () => {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const page = Number(new URL(String(input), 'http://localhost').searchParams.get('page'))
    requests.push(page)
    if (page > 3) throw new Error(`Unexpected page ${page}`)
    return servePage(page, largeWardrobe)
  })
  const picker = openPicker()
  await picker.findByText(grayShoe.name!)
  expect((picker.getByRole('combobox') as HTMLSelectElement).options).toHaveLength(273)
  expect(requests).toEqual([1, 2, 3])
  fireEvent.click(picker.getByRole('button', { name: 'Close' }))
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  const reopened = within(screen.getByRole('dialog'))
  await reopened.findByText(grayShoe.name!)
  expect((reopened.getByRole('combobox') as HTMLSelectElement).options).toHaveLength(273)
  fireEvent.change(reopened.getByRole('combobox'), { target: { value: grayShoe.id } })
  expect(reopened.getByRole('button', { name: 'wore' })).toBeEnabled()
  expect(requests).toEqual([1, 2, 3])
})

it('loads pages in order under StrictMode with page one already cached', async () => {
  client.setQueryData(queryKeys.items.list(filters, 1, 100), await servePage(1, largeWardrobe).json())
  let finishSecondPage!: (response: Response) => void
  vi.mocked(fetch).mockImplementation(async (input) => {
    const page = Number(new URL(String(input), 'http://localhost').searchParams.get('page'))
    requests.push(page)
    if (page === 2) return new Promise((resolve) => { finishSecondPage = resolve })
    if (page === 3) return servePage(3, largeWardrobe)
    throw new Error(`Unexpected page ${page}`)
  })
  render(<StrictMode><WearActions outfit={outfit} date="2026-10-24" /></StrictMode>, { wrapper })
  fireEvent.click(screen.getByRole('button', { name: 'swap' }))
  await waitFor(() => expect(requests).toContain(2))
  expect(requests).toEqual([2])
  await act(async () => finishSecondPage(servePage(2, largeWardrobe)))
  await screen.findByText(grayShoe.name!)
  expect(requests).toEqual([2, 3])
})

it('stops at an empty page even when it reports more pages and permits a swap', async () => {
  stubItems(() => Response.json({ items: [], total: 173, page: 2, page_size: 100, has_more: true }))
  const picker = openPicker()
  await waitFor(() => expect(requests).toContain(2))
  await waitFor(() => expect(picker.queryByRole('status')).not.toBeInTheDocument())
  expect(picker.queryByRole('alert')).not.toBeInTheDocument()
  fireEvent.change(picker.getByRole('combobox'), { target: { value: boots.id } })
  expect(picker.getByRole('button', { name: 'wore' })).toBeEnabled()
  expect(requests).toEqual([1, 2])
})

it('does not advance cached pages while the session is loading', async () => {
  sessionMock.status = 'loading'
  client.setQueryData(queryKeys.items.list(filters, 1, 100), await servePage(1, largeWardrobe).json())
  const picker = openPicker()
  fireEvent.change(picker.getByRole('combobox'), { target: { value: boots.id } })
  expect(picker.getByRole('button', { name: 'wore' })).toBeDisabled()
  const cachedPages = client.getQueryCache().findAll({ queryKey: queryKeys.items.all })
  expect(cachedPages).toHaveLength(1)
  expect(fetch).not.toHaveBeenCalled()
})
