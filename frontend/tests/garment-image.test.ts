import { describe, expect, it } from 'vitest'
import { garmentImageUrl } from '@/lib/garment-image'

describe('garment image size', () => {
  const images = { image_url: '/original', medium_url: '/medium', thumbnail_url: '/thumb' }
  it('selects thumb, medium and original by display size', () => {
    expect(garmentImageUrl(images, 'small')).toBe('/thumb')
    expect(garmentImageUrl(images, 'card')).toBe('/medium')
    expect(garmentImageUrl(images, 'detail')).toBe('/original')
  })
  it('falls back from medium to original and from thumb to medium', () => {
    expect(garmentImageUrl({ ...images, medium_url: null }, 'card')).toBe('/original')
    expect(garmentImageUrl({ ...images, thumbnail_url: null }, 'small')).toBe('/medium')
  })
  it('returns null for missing or empty images without retaining state', () => {
    expect(garmentImageUrl(images, 'card')).toBe('/medium')
    for (const empty of [null, undefined, {}, { image_url: '', medium_url: '', thumbnail_url: '' }]) {
      expect(garmentImageUrl(empty, 'card')).toBeNull()
    }
  })
  it('returns an available URL for every nonempty combination and size', () => {
    for (let mask = 1; mask < 8; mask++) {
      const available = {
        image_url: mask & 1 ? '/original' : null,
        medium_url: mask & 2 ? '/medium' : null,
        thumbnail_url: mask & 4 ? '/thumb' : null,
      }
      for (const size of ['small', 'card', 'detail'] as const) {
        expect(Object.values(available)).toContain(garmentImageUrl(available, size))
        expect(garmentImageUrl(available, size)).not.toBeNull()
      }
    }
  })
})
