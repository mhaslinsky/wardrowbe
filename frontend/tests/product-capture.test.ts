import { describe, expect, it } from 'vitest';
import { captureProduct, validateCapture } from '../../extension/capture';

describe('product capture', () => {
  it('reads nested JSON-LD variants and relative images without inventing missing fields', () => {
    document.body.innerHTML = `<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Linen shirt","brand":{"name":"Example"},"image":["/shirt.jpg"],"material":"80% linen, 20% cotton","offers":{"price":"29.99","priceCurrency":"USD"},"hasVariant":[{"@type":"Product","size":"XL","color":"Blue"}]}]}</script>`;
    const result = captureProduct();
    expect(result).toMatchObject({ name: 'Linen shirt', brand: 'Example', price: '29.99', currency: 'USD', sizes: ['XL'], colors: ['Blue'], material_detail: '80% linen, 20% cotton' });
    expect(result.images).toEqual([new URL('/shirt.jpg', location.href).href]);
    expect(result.selected_size).toBeUndefined();
    expect(validateCapture(result)).toEqual(result);
  });
  it('continues after malformed JSON-LD and rejects empty non-product pages', () => {
    document.body.innerHTML = '<script type="application/ld+json">{bad</script>';
    expect(() => captureProduct()).toThrow('No product');
    document.body.innerHTML += '<script type="application/ld+json">[{"@type":"Product","name":"Shirt"}]</script>';
    expect(captureProduct().images).toEqual([]);
  });
  it('bounds untrusted input and rejects unsafe URLs', () => {
    const capture = { source_url: 'https://store.example/shirt', images: [], sizes: [], colors: [] };
    expect(() => validateCapture({ ...capture, name: 'x'.repeat(101) })).toThrow();
    expect(() => validateCapture({ ...capture, images: ['javascript:alert(1)'] })).toThrow();
    expect(() => validateCapture({ ...capture, source_url: 'https://user:pass@store.example' })).toThrow();
    expect(() => validateCapture(null)).toThrow();
    expect(() => validateCapture({ ...capture, images: Array(31).fill('https://store.example/image') })).toThrow();
  });
});

import { readFileSync } from 'node:fs';
import { vi } from 'vitest';
import { confirmedPayload } from '../../extension/confirmation';
const fixtures = `${process.cwd()}/tests/fixtures/products/`;

describe('saved public product pages', () => {
  function visibleLayout() {
    return vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(function (this: Element) {
      return (this.closest('[hidden]') ? [] : [{ width: 100, height: 30 }]) as unknown as DOMRectList;
    });
  }
  it('captures Amazon text details without using related-product prices', () => {
    const layout = visibleLayout();
    document.body.innerHTML = readFileSync(`${fixtures}amazon.html`, 'utf8');
    const result = captureProduct();
    expect(result.name).toBe("Amazon Essentials Men's Slim-Fit Short-Sleeve Crewneck Pocket T-Shirt, Pack of 2");
    expect(result.material_detail).toContain('60% Cotton, 40% Polyester');
    expect(result.care).toContain('Machine Wash');
    expect(result.fit).toContain('Fits close to the body');
    expect(result.size_chart).toContain('Chest');
    expect(result.price).toBeUndefined();
    expect(result.currency).toBeUndefined();
    layout.mockRestore();
  });
  it('reads Allbirds ProductGroup before its size variants and preserves full image URLs', () => {
    document.body.innerHTML = readFileSync(`${fixtures}allbirds.html`, 'utf8');
    const result = captureProduct();
    expect(result.name).toBe("Men's Wool Runner");
    expect(result.brand).toBe('Allbirds');
    expect(result.price).toBe('110');
    expect(result.currency).toBe('USD');
    expect(result.sizes).toContain('14');
    expect(result.images[0]).toBe('https://www.allbirds.com/cdn/shop/files/Allbirds_WL_RN_SF_PDP_Natural_Grey_LAT.png?v=1751143404&width=100');
  });
  it('uses visible selections and ignores hidden variants', () => {
    const layout = visibleLayout();
    document.body.innerHTML = `<h1>Shirt</h1><img itemprop="image" src="/front.jpg"><select name="size"><option value="">Select size</option><option value="XL" selected>XL</option><option value="L">L</option></select><div hidden><select name="color"><option selected>Red</option></select></div><select name="color"><option value="blue" selected>Blue</option></select><p>Composition: 80% cotton, 20% linen</p><p>Machine wash cold</p><p>Relaxed fit</p><p>Size chart: chest 44, waist 38</p>`;
    expect(captureProduct()).toMatchObject({ selected_size: 'XL', selected_color: 'Blue', sizes: ['XL', 'L'], colors: ['Blue'], material_detail: 'Composition: 80% cotton, 20% linen', care: 'Machine wash cold', fit: 'Relaxed fit' });
    layout.mockRestore();
  });
});

describe('confirmed considering payload', () => {
  const capture = { source_url: 'https://store.example/shirt', name: 'Shirt', images: ['https://store.example/front.jpg'], sizes: ['L', 'XL'], colors: ['blue'], selected_size: 'XL', selected_color: 'blue', material_detail: '80% cotton, 20% linen', material: 'cotton', care: 'Machine wash cold', fit: 'Relaxed fit', size_chart: 'Chest 44' };
  it('separates purchase fields from confirmed tags and notes', () => {
    expect(confirmedPayload(capture)).toEqual({
      create: { image_urls: capture.images, source_url: capture.source_url, name: 'Shirt', brand: undefined, price: undefined, currency: undefined, planned_size: 'XL' },
      update: { tags: { material_detail: '80% cotton, 20% linen', material: 'cotton', size: 'XL', care_instructions: 'Machine wash cold', colors: ['blue'] }, notes: 'Relaxed fit\nChest 44\nAvailable sizes: L, XL\nAvailable colors: blue' },
    });
  });
  it('requires one to five images and supported metadata, with missing fields left out', () => {
    for (const images of [[], Array(6).fill(capture.images[0])]) expect(() => confirmedPayload({ ...capture, images })).toThrow('images');
    expect(() => confirmedPayload({ ...capture, material: 'plastic' })).toThrow('material');
    expect(() => confirmedPayload({ ...capture, selected_color: 'Moonlight Blue' })).toThrow('colors');
    expect(() => confirmedPayload({ ...capture, price: '$20' })).toThrow('price');
    expect(() => confirmedPayload({ ...capture, currency: 'usd' })).toThrow('currency');
    expect(JSON.parse(JSON.stringify(confirmedPayload({ source_url: capture.source_url, images: capture.images, sizes: [], colors: [] })))).toEqual({ create: { image_urls: capture.images, source_url: capture.source_url }, update: { tags: {}, notes: '' } });
  });
});
