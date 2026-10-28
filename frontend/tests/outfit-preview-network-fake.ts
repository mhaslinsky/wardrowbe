import { beforeEach, vi } from 'vitest';
import type { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

function previewNetworkState(): ReturnType<typeof useOutfitPreview> {
  return {
    preview: { image_url: '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg', medium_url: '/photos/users/me/preview-photo/files/11111111-1111-4111-8111-111111111111/previews/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_medium.jpg', created_at: '2026-10-28', stale: false },
    pending: false,
    error: false,
    conflict: false,
    comparisonPending: false,
    comparisonError: false,
    missingPhoto: false,
    likeConflict: false,
    draw: vi.fn(async () => true),
    remove: vi.fn(async () => true),
    like: vi.fn(async () => true),
    compare: vi.fn(async () => null),
  };
}

export const outfitPreviewNetworkFake = previewNetworkState();

beforeEach(() => Object.assign(outfitPreviewNetworkFake, previewNetworkState()));
