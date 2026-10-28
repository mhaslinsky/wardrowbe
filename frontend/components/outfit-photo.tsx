'use client';

import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { api, API_BASE_PATH, getAccessToken } from '@/lib/api';
import { ACCEPTED_IMAGE_INPUT } from '@/lib/image-types';
import { invalidateOutfitCaches } from '@/lib/hooks/cache-invalidation';
import type { Outfit } from '@/lib/types';

export function OutfitPhoto({ outfit }: { outfit: Outfit }) {
  const translate = useTranslations('outfits.photo');
  const client = useQueryClient();
  const { data: session } = useSession();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const photo = outfit.selfie_photo;
  const update = async (action: 'upload' | 'remove' | 'choice', value?: File | string) => {
    if (pending) return;
    setPending(true); setFailed(false);
    try {
      const path = `/outfits/${outfit.id}/photo`;
      if (action === 'upload') {
        const body = new FormData(); body.append('image', value as File);
        const token = session?.accessToken || getAccessToken();
        const response = await fetch(`${API_BASE_PATH}${path}`, {
          method: 'PUT', body, credentials: 'include',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!response.ok) throw new Error('Photo upload failed');
      } else if (action === 'remove') await api.delete(path);
      else await api.patch(`${path}-choice`, { choice: value });
      invalidateOutfitCaches(client, outfit.id);
    } catch { setFailed(true); }
    finally { setPending(false); }
  };
  return <section className="min-w-0 space-y-3">
    <h2 className="font-medium">{translate('title')}</h2>
    {photo && <a href={photo.image_url} target="_blank" rel="noopener noreferrer">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={photo.medium_url || photo.image_url} alt={translate('title')} className="max-h-96 w-full rounded-lg bg-muted object-contain" />
    </a>}
    <label className="block text-sm">
      {translate('add')}
      <input type="file" aria-label={translate('add')} accept={ACCEPTED_IMAGE_INPUT} disabled={pending} onChange={(event) => {
        const file = event.target.files?.[0]; event.target.value = '';
        if (file) void update('upload', file);
      }} />
    </label>
    {photo && <Button variant="outline" disabled={pending} onClick={() => void update('remove')}>{translate('remove')}</Button>}
    <label className="flex items-center gap-2 text-sm">
      {translate('choice')}
      <select aria-label={translate('choice')} value={outfit.card_image_choice ?? 'automatic'} disabled={pending} onChange={(event) => void update('choice', event.target.value)}>
        <option value="automatic">{translate('automatic')}</option>
        <option value="photo">{translate('photo')}</option>
        <option value="preview">{translate('preview')}</option>
      </select>
    </label>
    {failed && <p role="alert" className="text-sm text-destructive">{translate('failed')}</p>}
  </section>;
}
