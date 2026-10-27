import { validateCapture, type ProductCapture } from './capture';
import { MATERIAL_VALUES, CLOTHING_COLORS } from '../frontend/lib/generated/garment-vocabulary';

export type ConfirmedProduct = ProductCapture & { material?: string };
export function confirmedPayload(input: unknown) {
  const capture = validateCapture(input);
  if (capture.images.length < 1 || capture.images.length > 5) throw Error('images');
  if (capture.price && (!/^\d{1,8}(\.\d{1,2})?$/.test(capture.price))) throw Error('price');
  if (capture.currency && !/^[A-Z]{3}$/.test(capture.currency)) throw Error('currency');
  const material = (input as ConfirmedProduct).material;
  if (material && !(MATERIAL_VALUES as readonly string[]).includes(material)) throw Error('material');
  const colors = capture.selected_color ? [capture.selected_color] : capture.colors;
  if (colors.some(color => !CLOTHING_COLORS.some(entry => entry.value === color))) throw Error('colors');
  return {
    create: { image_urls: capture.images, source_url: capture.source_url, name: capture.name, brand: capture.brand,
      price: capture.price || undefined, currency: capture.currency || undefined, planned_size: capture.selected_size },
    update: { tags: { material_detail: capture.material_detail, material: material || undefined,
      size: capture.selected_size, care_instructions: capture.care, colors: colors.length ? colors : undefined },
    notes: [capture.fit, capture.size_chart, capture.sizes.length ? `Available sizes: ${capture.sizes.join(', ')}` : '',
      capture.colors.length ? `Available colors: ${capture.colors.join(', ')}` : ''].filter(Boolean).join('\n').slice(0, 10000) },
  };
}
