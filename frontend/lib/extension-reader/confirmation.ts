import { validateCapture, type ProductCapture } from './capture';
import { MATERIAL_VALUES } from '../generated/garment-vocabulary';
import { normalizeColor } from '../colors';

export type ConfirmedProduct = ProductCapture & { material?: string };
export function confirmedPayload(input: unknown) {
  const capture = validateCapture(input);
  if (capture.images.length < 1 || capture.images.length > 5) throw Error('images');
  if (capture.price && !/^\d{1,8}(\.\d{1,2})?$/.test(capture.price)) throw Error('price');
  if ((capture.price && !capture.currency) || (capture.currency && !/^[A-Z]{3}$/.test(capture.currency))) throw Error('currency');
  const material = (input as ConfirmedProduct).material;
  if (material !== undefined && (typeof material !== 'string' || !(MATERIAL_VALUES as readonly string[]).includes(material.trim()))) throw Error('material');
  const color = capture.selected_color ? normalizeColor(capture.selected_color) : null;
  const notes = [capture.selected_color && !color ? `Selected color: ${capture.selected_color}` : '', capture.fit, capture.size_chart, capture.sizes.length ? `Available sizes: ${capture.sizes.join(', ')}` : '',
    capture.colors.length ? `Available colors: ${capture.colors.join(', ')}` : ''].filter(Boolean).join('\n').slice(0, 10000);
  return {
    create: { image_urls: capture.images, source_url: capture.source_url, name: capture.name, brand: capture.brand,
      price: capture.price, currency: capture.currency, planned_size: capture.selected_size },
    update: { tags: { material_detail: capture.material_detail, material: material?.trim(),
      size: capture.selected_size, care_instructions: capture.care, colors: color ? [color] : undefined },
    notes: notes || undefined },
  };
}
