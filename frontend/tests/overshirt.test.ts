import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, it } from 'vitest';
import { SUPPORTED_LOCALES } from '@/lib/i18n/locales';
import { CLOTHING_TYPES, CLOTHING_SUBTYPES } from '@/lib/types';
import { canonicalItemOrder, ITEM_ROLE, slotsForType } from '@/lib/studio/canonical-order';

it('offers overshirts as layers alongside a base top and outerwear', () => {
  expect(CLOTHING_TYPES.map((type) => type.value)).toContain('overshirt');
  expect(CLOTHING_SUBTYPES.overshirt).toEqual(['utility', 'shirt-jacket', 'shacket', 'chore-jacket']);
  expect(ITEM_ROLE.overshirt).toBe('mid_layer');
  expect(slotsForType('overshirt')).toEqual(['mid_layer']);
  expect(canonicalItemOrder([
    { id: 'coat', type: 'coat' }, { id: 'layer', type: 'overshirt' },
    { id: 'tee', type: 't-shirt' },
  ]).map((item) => item.id)).toEqual(['tee', 'layer', 'coat']);
});

it.each(SUPPORTED_LOCALES)('translates the overshirt label in %s', (locale) => {
  const labels = JSON.parse(readFileSync(resolve(__dirname, '../messages', locale, 'constants.json'), 'utf8'));
  expect(labels.types.overshirt).toBeTruthy();
  if (locale !== 'en') expect(labels.types.overshirt).not.toBe('Overshirt');
  for (const subtype of CLOTHING_SUBTYPES.overshirt) {
    expect(labels.subtypes[subtype]).toBeTruthy();
  }
});
