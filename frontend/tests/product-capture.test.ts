import { describe, expect, it } from 'vitest';
import { captureProduct, validateCapture } from '../../extension/capture';

describe('product capture', () => {
  it('reads nested JSON-LD variants and relative images without inventing missing fields', () => {
    document.body.innerHTML = `<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Linen shirt","brand":{"name":"Example"},"image":["/shirt.jpg"],"material":"80% linen, 20% cotton","offers":{"price":"29.99","priceCurrency":"USD"},"hasVariant":[{"@type":"Product","size":"XL","color":"Blue"}]}]}</script>`;
    const result = captureProduct();
    expect(result).toMatchObject({ name: 'Linen shirt', brand: 'Example', price: '29.99', currency: 'USD', sizes: ['XL'], colors: ['Blue'], material_detail: '80% linen, 20% cotton' });
    expect(result.images).toEqual([new URL('/shirt.jpg', location.href).href]);
    expect(result.selected_size).toBeUndefined();
    expect(result.selected_color).toBeUndefined();
    expect(result.size_chart).toBeUndefined();
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
function visibleLayout() {
    return vi.spyOn(Element.prototype, 'getClientRects').mockImplementation(function (this: Element) {
      return (this.closest('[hidden]') ? [] : [{ width: 100, height: 30 }]) as unknown as DOMRectList;
    });
  }


describe('saved public product pages', () => {
  it('captures Amazon text details without using related-product prices', () => {
    const layout = visibleLayout();
    document.body.innerHTML = readFileSync(`${fixtures}amazon.html`, 'utf8');
    const result = captureProduct();
    expect(result.name).toBe("Amazon Essentials Men's Slim-Fit Short-Sleeve Crewneck Pocket T-Shirt, Pack of 2");
    expect(result.material_detail).toContain('60% Cotton, 40% Polyester');
    expect(result.care).toContain('Machine Wash');
    expect(result.fit).toContain('Fits close to the body');
    expect(result.size_chart).toContain('X-Large17 - 17.546 - 4840.5 - 42.5');
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
    expect(confirmedPayload({ ...capture, selected_color: 'Moonlight Blue' }).update.tags.colors).toBeUndefined();
    expect(() => confirmedPayload({ ...capture, price: '$20' })).toThrow('price');
    expect(() => confirmedPayload({ ...capture, currency: 'usd' })).toThrow('currency');
    expect(JSON.parse(JSON.stringify(confirmedPayload({ source_url: capture.source_url, images: capture.images, sizes: [], colors: [] })))).toEqual({ create: { image_urls: capture.images, source_url: capture.source_url }, update: { tags: {} } });
  });
});

describe('Amazon live capture regressions', () => {
  function amazon() {
    document.body.innerHTML = readFileSync(`${fixtures}amazon-live.html`, 'utf8');
    const layout = visibleLayout();
    try { return captureProduct(); } finally { layout.mockRestore(); }
  }
  it('prefers the product title over the preceding accessibility summary', () => {
    expect(amazon().name).toBe("Amazon Essentials Men's Slim-Fit Short-Sleeve Crewneck Pocket T-Shirt, Pack of 2");
  });
  it('reads the fabric table value without labels or review-star rows', () => {
    expect(amazon().material_detail).toBe('Solids: 100% Cotton; Heathers: 60% Cotton, 40% Polyester');
  });
  it('keeps fit notes inside product details and excludes inert markup', () => {
    const fit = amazon().fit;
    expect(fit).toBe('Fits close to the body for fitted, slim silhouette');
    expect(fit).not.toMatch(/Golf Pant|<img|src=/);
  });
  it('keeps the product photo and excludes navigation SVG images', () => {
    expect(amazon().images).toEqual(['https://m.media-amazon.com/images/I/71a1XwGxVqL._AC_SX425_.jpg']);
  });
});

describe('reviewed capture and confirmation contracts', () => {
  function page(html: string) {
    document.body.innerHTML = html;
    const layout = visibleLayout();
    try { return captureProduct(); } finally { layout.mockRestore(); }
  }
  const base = { source_url: 'https://store.example/shirt', images: ['https://store.example/front.jpg'], sizes: [], colors: [] };
  it('uses the selected ProductGroup variant for offer currency and images', () => {
    const variants = [{ '@type': 'Product', size: 'M', color: 'Blue', offers: { price: '10', priceCurrency: 'USD' }, image: 'https://store.example/blue.jpg' }, { '@type': 'Product', size: 'L', color: 'Red', offers: { price: '20', priceCurrency: 'GBP' }, image: 'https://store.example/red.jpg' }];
    const result = page(`<main><select name="size"><option selected>L</option></select><select name="color"><option selected>Red</option></select><script type="application/ld+json">${JSON.stringify({ '@type': 'ProductGroup', name: 'Shirt', offers: { price: '10', priceCurrency: 'USD' }, image: 'https://store.example/blue.jpg', hasVariant: variants })}</script></main>`);
    expect(result).toMatchObject({ selected_size: 'L', selected_color: 'Red', price: '20', currency: 'GBP', images: ['https://store.example/red.jpg'] });
  });
  it('reads a price and currency from the same specification and rejects malformed currencies', () => {
    const product = (offers: unknown) => `<script type="application/ld+json">${JSON.stringify({ '@type': 'Product', name: 'Shirt', offers })}</script>`;
    expect(page(product({ priceSpecification: [{ price: 50, priceCurrency: 'gbp' }] }))).toMatchObject({ price: '50', currency: 'GBP' });
    for (const offers of [{ price: 50 }, { price: 50, priceCurrency: 'USDX' }, { price: {}, priceCurrency: 'USD' }, { price: '$10 - $20', priceCurrency: 'USD' }]) {
      const result = page(product(offers)); expect(result.price).toBeUndefined(); expect(result.currency).toBeUndefined();
    }
    expect(() => confirmedPayload({ ...base, price: '50' })).toThrow('currency');
  });
  it('ignores recommendation prices, fabric, fit and selected variants outside the product container', () => {
    const result = page('<aside><span itemprop="price">199</span><span itemprop="priceCurrency">EUR</span><p>Fabric: polyester</p><p>Regular fit</p><select name="size"><option selected>L</option></select></aside><main data-product-id="shirt"><h1>Shirt</h1><img itemprop="image" src="/front.jpg"></main>');
    expect(result.price).toBeUndefined(); expect(result.currency).toBeUndefined(); expect(result.material_detail).toBeUndefined(); expect(result.fit).toBeUndefined(); expect(result.selected_size).toBeUndefined();
  });
  it('only assigns a selected canonical colour, preserving available colours in notes', () => {
    expect(confirmedPayload({ ...base, colors: ['Red', 'Blue'] }).update.tags.colors).toBeUndefined();
    for (const [selected_color, expected] of [['Light Blue', 'light-blue'], ['Grey', 'gray'], ['  BLUE  ', 'blue']]) expect(confirmedPayload({ ...base, selected_color }).update.tags.colors).toEqual([expected]);
    const unknown = confirmedPayload({ ...base, selected_color: 'Moonlight Blue', colors: ['Moonlight Blue', 'Red'] });
    expect(unknown.update.tags.colors).toBeUndefined(); expect(unknown.update.notes).toContain('Available colors: Moonlight Blue, Red');
  });
  it('rejects blank scalar and list fields, trims values and omits empty notes', () => {
    for (const key of ['name', 'brand', 'price', 'currency', 'selected_size', 'selected_color', 'material_detail', 'care', 'fit', 'size_chart']) expect(() => validateCapture({ ...base, [key]: '  ' })).toThrow();
    expect(() => validateCapture({ ...base, sizes: [' '] })).toThrow();
    expect(validateCapture({ ...base, name: ' Shirt ' }).name).toBe('Shirt');
    expect(() => confirmedPayload({ ...base, material: ' ' })).toThrow('material');
    expect(JSON.parse(JSON.stringify(confirmedPayload(base))).update).toEqual({ tags: {} });
  });
  it('does not treat disabled or implicit first options as selected sizes', () => {
    for (const options of ['<option selected disabled value="-1">Select size</option><option>L</option>', '<option>S</option><option>M</option>']) {
      expect(page(`<main><h1>Shirt</h1><img itemprop="image" src="/front.jpg"><select name="size">${options}</select></main>`).selected_size).toBeUndefined();
    }
  });
  it('checks normalized URL size and does not resolve empty images to the page URL', () => {
    expect(() => validateCapture({ ...base, images: [`https://store.example/${'é'.repeat(400)}`] })).toThrow('URL');
    expect(page('<script type="application/ld+json">{"@type":"Product","name":"Shirt"}</script><img itemprop="image" src="">').images).toEqual([]);
  });
  it('passes real fixture captures through confirmation and carries fabric exactly', () => {
    for (const [fixture, expectedMaterial] of [['amazon.html', 'Solids: 100% Cotton; Heathers: 60% Cotton, 40% Polyester'], ['amazon-live.html', 'Solids: 100% Cotton; Heathers: 60% Cotton, 40% Polyester'], ['allbirds.html', undefined]] as const) {
      const capture = page(readFileSync(`${fixtures}${fixture}`, 'utf8'));
      const confirmed = confirmedPayload({ ...capture, images: capture.images.slice(0, 5) });
      expect(confirmed.update.tags.colors).toBeUndefined();
      expect(confirmed.update.tags.material_detail).toBe(expectedMaterial);
    }
  });
});

describe('page product and DOM offer resolution', () => {
  function capture(html: string) {
    document.body.innerHTML = html;
    const layout = visibleLayout();
    try { return captureProduct(); } finally { layout.mockRestore(); }
  }
  it('resolves the page mainEntity or matching URL before a recommended ProductGroup', () => {
    const main = { '@type': 'Product', '@id': location.href, name: 'Page shirt', image: 'https://store.example/page.jpg', offers: { price: '30', priceCurrency: 'USD' } };
    const recommendation = { '@type': 'ItemList', itemListElement: [{ '@type': 'ProductGroup', name: 'Recommended coat', image: 'https://store.example/coat.jpg', offers: { price: '199', priceCurrency: 'EUR' } }] };
    const group = recommendation.itemListElement[0];
    const graphPages = [[{ '@type': 'WebPage', mainEntity: main }, group], [recommendation, main], [group, main]].map(graph => `<script type="application/ld+json">${JSON.stringify({ '@graph': graph })}</script>`);
    const contained = { ...main, '@id': undefined };
    const containedPage = `<script type="application/ld+json">${JSON.stringify(group)}</script><main><h1>Page shirt</h1><script type="application/ld+json">${JSON.stringify(contained)}</script></main>`;
    for (const html of [...graphPages, containedPage]) expect(capture(html)).toMatchObject({ name: 'Page shirt', price: '30', currency: 'USD', images: ['https://store.example/page.jpg'] });
  });
  it('resolves a single-object hasVariant without requiring a back-reference', () => {
    const variant = { '@type': 'Product', size: 'L', color: 'Red', image: 'https://store.example/red.jpg', offers: { price: '20', priceCurrency: 'GBP' } };
    const group = { '@type': 'ProductGroup', name: 'Shirt', hasVariant: variant, image: 'https://store.example/blue.jpg', offers: { price: '10', priceCurrency: 'USD' } };
    expect(capture(`<main><select name="size"><option selected>L</option></select><select name="color"><option selected>Red</option></select><script type="application/ld+json">${JSON.stringify(group)}</script></main>`)).toMatchObject({ price: '20', currency: 'GBP', images: ['https://store.example/red.jpg'], sizes: ['L'], colors: ['Red'] });
  });
  it('pairs DOM price and currency within one offer and omits unpaired offers', () => {
    const product = (offers: string) => `<main><h1>Shirt</h1><img itemprop="image" src="/front.jpg">${offers}</main>`;
    expect(capture(product('<div itemprop="offers"><span itemprop="price">80</span></div><div itemprop="offers"><span itemprop="price">20</span><meta itemprop="priceCurrency" content="EUR"></div>'))).toMatchObject({ price: '20', currency: 'EUR' });
    const unpaired = capture(product('<div itemprop="offers"><span itemprop="price">80</span></div><div itemprop="offers"><span itemprop="priceCurrency">EUR</span></div>'));
    expect(unpaired.price).toBeUndefined(); expect(unpaired.currency).toBeUndefined();
  });
  it('reads fabric, care and fit from a leaf product description', () => {
    const description = 'Composition: 80% cotton, 20% linen. Machine wash cold. Relaxed fit.';
    const result = capture(`<main><h1>Shirt</h1><img itemprop="image" src="/front.jpg"><p itemprop="description">${description}</p></main>`);
    expect(result.material_detail).toBe(description); expect(result.care).toBe(description); expect(result.fit).toBe(description);
  });
});
