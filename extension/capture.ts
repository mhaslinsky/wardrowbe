export interface ProductCapture {
  source_url: string;
  name?: string;
  brand?: string;
  price?: string;
  currency?: string;
  selected_size?: string;
  selected_color?: string;
  sizes: string[];
  colors: string[];
  material_detail?: string;
  care?: string;
  fit?: string;
  size_chart?: string;
  images: string[];
}

export function validateCapture(input: unknown): ProductCapture {
  if (!input || typeof input !== 'object' || JSON.stringify(input).length > 32000) throw Error('Invalid capture');
  const source = input as Record<string, unknown>;
  const result: Record<string, unknown> = {};
  const limits = { source_url: 2000, name: 100, brand: 100, price: 24, currency: 3, selected_size: 32,
    selected_color: 100, material_detail: 1000, care: 255, fit: 2000, size_chart: 6000 };
  const http = (value: string) => {
    const parsed = new URL(value);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.href.length > 2000) throw Error('Invalid URL');
    return parsed.href;
  };
  for (const [key, limit] of Object.entries(limits)) {
    if (source[key] === undefined) continue;
    if (typeof source[key] !== 'string' || !source[key].trim() || source[key].length > limit) throw Error('Invalid field');
    result[key] = source[key].trim();
  }
  if (typeof result.source_url !== 'string') throw Error('Missing source');
  result.source_url = http(result.source_url);
  for (const key of ['sizes', 'colors', 'images']) {
    const values = source[key];
    if (!Array.isArray(values) || values.length > 30 || values.some(value => typeof value !== 'string' || !value.trim() || value.length > (key === 'images' ? 2000 : 100))) throw Error('Invalid list');
    result[key] = key === 'images' ? values.map(http) : values.map(value => value.trim());
  }
  return result as unknown as ProductCapture;
}

export function captureProduct(): ProductCapture {
  const text = (value: unknown, max = 100): string | undefined => typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : undefined;
  const values = (value: unknown, max = 100): string[] => (Array.isArray(value) ? value : [value]).flatMap(entry => {
    const content = text(typeof entry === 'object' && entry ? entry.name : entry, max);
    return content ? [content] : [];
  }).slice(0, 30);
  const products: Record<string, any>[] = [];
  const visit = (value: any, depth = 0) => {
    if (!value || depth > 12) return;
    if (Array.isArray(value)) return value.slice(0, 100).forEach(entry => visit(entry, depth + 1));
    if (typeof value !== 'object') return;
    const types = Array.isArray(value['@type']) ? value['@type'] : [value['@type']];
    if (types.includes('Product') || types.includes('ProductGroup')) products.push(value);
    for (const key of ['@graph', 'hasVariant', 'mainEntity', 'itemListElement', 'item']) visit(value[key], depth + 1);
  };
  for (const script of Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0, 30)) {
    if ((script.textContent?.length || 0) > 200000) continue;
    try { visit(JSON.parse(script.textContent || 'null')); } catch { continue; }
  }
  const visible = (element: Element) => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden' && !element.closest('[hidden], [aria-hidden="true"]');
  const anchor = document.querySelector('#productTitle') || document.querySelector('[itemprop="name"]') || Array.from(document.querySelectorAll('h1')).find(visible);
  const root = anchor?.closest('#dp, #ppd, [itemtype*="Product"], [data-product-id], main, article') || document.querySelector('#dp, #ppd, main, [itemtype*="Product"], [data-product-id]') || document.body;
  const excluded = (element: Element) => !!element.closest('script, style, noscript, template, nav, footer, .a-carousel-card, #reviewsMedley, [id*="recommend" i], [class*="recommend" i], [id*="sims" i]');
  const content = (element: Element | null) => {
    if (!element) return undefined;
    const copy = element.cloneNode(true) as Element;
    for (const child of Array.from(copy.querySelectorAll('script, style, noscript, template, [hidden], [aria-hidden="true"]'))) child.remove();
    return copy.textContent?.replace(/\s+/g, ' ').trim();
  };
  const read = (selector: string, max = 100) => {
    for (const candidate of selector.split(',')) {
      const element = Array.from(root.querySelectorAll(candidate)).find(element => visible(element) && !excluded(element));
      const value = text(element?.getAttribute('content') || content(element || null), max);
      if (value) return value;
    }
  };
  const selected = (kind: string) => {
    const select = Array.from(root.querySelectorAll<HTMLSelectElement>(`select[name*="${kind}" i], select[id*="${kind}" i]`)).find(element => visible(element) && !excluded(element));
    const chosen = select?.selectedOptions[0];
    const valid = chosen && !chosen.disabled && chosen.value.trim() && !/^(select|choose)\b/i.test(chosen.textContent?.trim() || '');
    return (valid && (chosen.defaultSelected || select.selectedIndex > 0) ? text(chosen.textContent) : undefined) || read(`[aria-label*="${kind}" i] [aria-checked="true"], [id*="${kind}" i] .selection`);
  };
  const options = (kind: string) => Array.from(root.querySelectorAll(`select[name*="${kind}" i] option, select[id*="${kind}" i] option, [aria-label*="${kind}" i] [role="radio"]`))
    .filter(element => !excluded(element) && (element instanceof HTMLOptionElement ? !element.disabled && !!element.value.trim() && !/^(select|choose)\b/i.test(element.textContent?.trim() || '') && !!element.closest('select') && visible(element.closest('select')!) : visible(element)))
    .flatMap(element => values(element.textContent)).slice(0, 30);
  const hasType = (entry: Record<string, any>, kind: string) => (Array.isArray(entry['@type']) ? entry['@type'] : [entry['@type']]).includes(kind);
  const product = products.find(entry => hasType(entry, 'ProductGroup')) || products[0] || {};
  const selectedSize = selected('size')?.slice(0, 32);
  const selectedColor = selected('color');
  const variants = products.filter(entry => entry !== product && (Array.isArray(product.hasVariant) ? product.hasVariant.includes(entry) : entry.isVariantOf?.['@id'] === product['@id'] && !!product['@id']));
  const key = (value: string) => value.trim().toLowerCase().replace(/\s+/g, ' ');
  const matches = variants.filter(entry => (!selectedSize || values(entry.size).some(value => key(value) === key(selectedSize))) && (!selectedColor || values(entry.color).some(value => key(value) === key(selectedColor))));
  const variant = (selectedSize || selectedColor) && matches.length === 1 ? matches[0] : undefined;
  const chosenProduct = variant || product;
  const array = (value: any): any[] => Array.isArray(value) ? value : value ? [value] : [];
  const pair = (value: any) => {
    const price = typeof value?.price === 'number' || typeof value?.price === 'string' ? String(value.price).trim() : '';
    const currency = typeof value?.priceCurrency === 'string' ? value.priceCurrency.trim().toUpperCase() : '';
    return /^\d{1,8}(\.\d{1,2})?$/.test(price) && /^[A-Z]{3}$/.test(currency) ? { price, currency } : undefined;
  };
  let purchase = array(chosenProduct.offers).flatMap(offer => [offer, ...array(offer.priceSpecification)]).map(pair).find(Boolean);
  if (!purchase && !variant) purchase = pair({ price: read('[itemprop="price"]', 24), priceCurrency: read('[itemprop="priceCurrency"]', 10) });
  // Recommendations and reviews can repeat product words without describing this item.
  const zones = Array.from(root.querySelectorAll('#productFactsDesktopExpander, #feature-bullets, [id*="productDetails"], [itemprop="description"], [class*="product-detail"], [class*="product-description"]'));
  const detailRows = Array.from(new Set((zones.length ? zones : [root]).flatMap(zone => Array.from(zone.querySelectorAll('li, tr, p, details, .product-facts-detail'))))).filter(element => visible(element) && !excluded(element));
  const rowValue = (label: RegExp, max: number) => {
    for (const row of detailRows) {
      const heading = row.querySelector('.a-col-left, th, dt') || row.children[0];
      const value = row.querySelector('.a-col-right, dd') || (row.children.length > 1 ? row.children[row.children.length - 1] : null);
      if (label.test(content(heading)?.replace(/:$/, '') || '')) {
        const result = text(content(value), max);
        if (result) return result;
      }
    }
  };
  const detail = (words: RegExp, max: number) => text(Array.from(new Set(detailRows.map(content).filter((value): value is string => !!value && value.length < max && words.test(value)))).slice(0, 5).join('\n'), max);
  const images = array(chosenProduct.image).flatMap(entry => array(typeof entry === 'object' && entry ? entry.url || entry.contentUrl : entry)).filter(value => typeof value === 'string' && value.trim() && value.length <= 2000);
  if (!images.length) {
    const selectors = '#landingImage, #imgTagWrapperId img, [itemprop="image"], [data-product-gallery] img, [class*="product-image"] img, [class*="product__media"] img';
    for (const element of Array.from(root.querySelectorAll<HTMLImageElement>(selectors)).filter(element => visible(element) && !excluded(element))) {
      const source = element.currentSrc || element.getAttribute('src')?.trim();
      if (source) images.push(source);
    }
  }
  const normalized = images.flatMap(value => {
    try {
      const url = new URL(value, location.href);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && url.href.length <= 2000 && !/\.svg$/i.test(url.pathname) ? [url.href] : [];
    } catch { return []; }
  });
  const name = values(product.name)[0] || read('#productTitle, [itemprop="name"], h1');
  if (!name || (!products.length && !normalized.length)) throw Error('No product found');
  if (location.href.length > 2000) throw Error('Source URL too long');
  const brand = values(chosenProduct.brand)[0] || values(product.brand)[0] || read('#bylineInfo, [itemprop="brand"]');
  return {
    source_url: location.href, name,
    brand: brand?.replace(/^Visit the (.+) Store$/i, '$1').replace(/^Brand:\s*/i, ''),
    price: purchase?.price, currency: purchase?.currency, selected_size: selectedSize, selected_color: selectedColor,
    sizes: Array.from(new Set([...values(product.size), ...options('size'), ...variants.flatMap(entry => values(entry.size))])).slice(0, 30),
    colors: Array.from(new Set([...values(product.color), ...options('color'), ...variants.flatMap(entry => values(entry.color))])).slice(0, 30),
    material_detail: values(chosenProduct.material, 1000)[0] || values(product.material, 1000)[0] || rowValue(/^(fabric(?: type)?|material(?:s| type)?|composition)$/i, 1000) || detail(/fabric|composition|material/i, 1000),
    care: rowValue(/^(care(?: instructions)?|product care instructions)$/i, 255) || detail(/machine wash|hand wash|dry clean|care instructions/i, 255),
    fit: detail(/\bfits?\b|runs small|runs large|true.to.size/i, 2000) || rowValue(/^fit(?: type)?$/i, 2000),
    size_chart: read('[id*="sizechart" i] table, [id*="size-chart" i] table', 6000) || read('[id*="sizechart" i], [id*="size-chart" i]', 6000) || detail(/size chart|chest.*waist|waist.*inseam/i, 6000),
    images: Array.from(new Set(normalized)).slice(0, 30),
  };
}
