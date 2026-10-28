import { beforeEach, vi } from 'vitest';
import type { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

function previewNetworkState(): ReturnType<typeof useOutfitPreview> {
  return {
    preview: { image_url: '/preview.jpg', medium_url: '/preview-medium.jpg', created_at: '2026-10-28', stale: false },
    pending: false,
    error: false,
    conflict: false,
    missingPhoto: false,
    likeConflict: false,
    draw: vi.fn(async () => true),
    remove: vi.fn(async () => true),
    like: vi.fn(async () => true),
  };
}

export const outfitPreviewNetworkFake = previewNetworkState();

beforeEach(() => Object.assign(outfitPreviewNetworkFake, previewNetworkState()));
