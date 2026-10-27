import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { OutfitCard } from '@/components/outfits/outfit-card'
import type { Outfit } from '@/lib/hooks/use-outfits'

vi.mock('@/lib/hooks/use-user', () => ({ useUserToday: () => () => '2026-10-24' }))
vi.mock('@/lib/hooks/use-outfit-title', () => ({ useOutfitTitle: () => () => 'Look' }))
vi.mock('@/lib/hooks/use-translated-constants', () => ({ useTypeLabel: () => (value: string) => value, useOccasionLabel: () => (value: string) => value }))

const outfit = (count: number) => ({
  id: 'look', source: 'manual', occasion: 'casual',
  items: Array.from({ length: count }, (_, index) => ({ id: `piece-${index}`, type: 'shirt', needs_wash: true })),
}) as Outfit

describe('outfit thumbnails', () => {
  it.each([1, 4, 5, 8, 9])('fills the grid for %i pieces', (count) => {
    const { container } = render(<OutfitCard outfit={outfit(count)} />)
    const grid = container.querySelector<HTMLElement>('.grid')!
    expect(grid.children).toHaveLength(Math.min(count, 8))
    expect(grid.style.gridTemplateColumns).toBe(`repeat(${Math.min(count, 4)}, minmax(0, 1fr))`)
    expect(grid.style.gridTemplateRows).toBe(count > 4 ? 'repeat(2, minmax(0, 1fr))' : 'minmax(0, 1fr)')
    expect(screen.getAllByText('shirt')).toHaveLength(count > 8 ? 7 : count)
    if (count > 8) expect(screen.getByText(`+${count - 7}`)).toBeVisible()
    else expect(screen.queryByText(/^\+/)).not.toBeInTheDocument()
  })
  it('hides wash warnings only when requested', () => {
    const { rerender } = render(<OutfitCard outfit={outfit(1)} />)
    expect(screen.getByText('wash')).toBeVisible()
    expect(screen.getByText('piecesInWash')).toBeVisible()
    rerender(<OutfitCard outfit={outfit(1)} hideWashWarnings />)
    expect(screen.queryByText('wash')).not.toBeInTheDocument()
    expect(screen.queryByText('piecesInWash')).not.toBeInTheDocument()
  })
  it('uses card width for single-line wash labels', () => {
    render(<OutfitCard outfit={outfit(1)} />)
    expect(screen.getByText('wash').parentElement).toHaveClass('whitespace-nowrap')
    expect(screen.getByText('inTheWash')).toHaveClass('[@container(min-width:480px)]:inline')
  })
})

it('purchase badge follows current unowned membership', () => {
  const { rerender } = render(<OutfitCard outfit={{ ...outfit(1), unowned_item_ids: ['piece-0'] }} />);
  expect(screen.getByText('unbought')).toBeVisible();
  rerender(<OutfitCard outfit={{ ...outfit(1), unowned_item_ids: [] }} />);
  expect(screen.queryByText('unbought')).not.toBeInTheDocument();
});
