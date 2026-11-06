import { expect, it, vi } from 'vitest';
import { initializePostHog, recordingOptions } from '@/lib/posthog';

const { init } = vi.hoisted(() => ({ init: vi.fn() }));
vi.mock('posthog-js', () => ({ default: { init } }));

it('without a key PostHog does not initialize', async () => {
  expect(await initializePostHog({}, 'masked.example')).toBeUndefined();
  expect(init).not.toHaveBeenCalled();
});

it('unmasked hosts keep defaults; other hosts block images and mask measurements', () => {
  expect(recordingOptions('allowed.example', ' other.example, ALLOWED.example ')).toEqual({});
  for (const hosts of ['', 'allowed.example', 'masked.example.evil']) {
    const options = recordingOptions('masked.example', hosts);
    document.body.innerHTML = '<img><picture></picture><canvas></canvas><video></video><section data-posthog-measurements><span>178 cm</span><input value="178"></section>';
    expect(Array.from(document.querySelectorAll(options.blockSelector!)).map((node) => node.tagName))
      .toEqual(['IMG', 'PICTURE', 'CANVAS', 'VIDEO']);
    expect(document.querySelector('span')!.matches(options.maskTextSelector!)).toBe(true);
    expect(document.querySelector('input')!.matches(options.maskTextSelector!)).toBe(true);
  }
});
