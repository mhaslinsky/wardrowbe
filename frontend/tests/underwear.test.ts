import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ITEM_ROLE, ROLE_VALUES } from '@/lib/generated/garment-vocabulary';
import { SUPPORTED_LOCALES } from '@/lib/i18n/locales';
import { canonicalItemOrder, slotsForType } from '@/lib/studio/canonical-order';
import { CLOTHING_TYPES, CLOTHING_SUBTYPES } from '@/lib/types';

describe('underwear', () => {
  it('is available in the picker with all six subtypes', () => {
    expect(CLOTHING_TYPES.map((type) => type.value)).toContain('underwear');
    expect(CLOTHING_SUBTYPES.underwear).toEqual([
      'boxer-briefs', 'briefs', 'boxers', 'trunks', 'compression', 'long-underwear',
    ]);
  });

  it.each(SUPPORTED_LOCALES)('labels underwear in %s', (locale) => {
    const labels = JSON.parse(readFileSync(
      resolve(__dirname, '..', 'messages', locale, 'constants.json'), 'utf8'
    ));
    for (const label of [labels.types.underwear, labels.roles.underwear,
      ...CLOTHING_SUBTYPES.underwear.map((subtype) => labels.subtypes[subtype])]) {
      expect(typeof label).toBe('string');
      expect(label.trim().length).toBeGreaterThan(0);
    }
  });

  it('has a canonical role without occupying outfit slots', () => {
    expect(ROLE_VALUES).toContain('underwear');
    expect(ITEM_ROLE.underwear).toBe('underwear');
    expect(slotsForType('underwear')).toEqual([]);
    const underwear = { id: 'underwear', type: 'underwear' };
    const tights = { id: 'tights', type: 'tights' };
    expect(canonicalItemOrder([underwear, tights])).toEqual([tights, underwear]);
    expect(slotsForType('tights')).toEqual(['legwear']);
  });
});
