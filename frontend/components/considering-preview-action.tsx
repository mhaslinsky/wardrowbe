'use client';

import { useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useSession } from 'next-auth/react';
import { useTranslations } from 'next-intl';
import Link from 'next/link';
import { api } from '@/lib/api';
import type { Outfit } from '@/lib/types';
import { applySessionToken } from '@/lib/hooks/use-session-token';
import { useItems } from '@/lib/hooks/use-items';
import { queryKeys } from '@/lib/hooks/query-keys';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { OutfitPreview } from '@/components/outfit-preview';

export function ConsideringPreviewAction({ itemId, disabled }: { itemId: string; disabled?: boolean }) {
  const t = useTranslations('wardrobe.considering');
  const saved = useRef(new Map<string, string>());
  const [open, setOpen] = useState(false);
  return <>
    <Button variant="outline" disabled={disabled} onClick={() => setOpen(true)}>{t('preview')}</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent aria-describedby={undefined} className="max-h-[90dvh] overflow-y-auto">
        <DialogTitle>{t('preview')}</DialogTitle>
        {open && <PreviewPicker itemId={itemId} saved={saved.current} close={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  </>;
}

function PreviewPicker({ itemId, close, saved }: { itemId: string; close: () => void; saved: Map<string, string> }) {
  const t = useTranslations('wardrobe.considering');
  const common = useTranslations('common');
  const [selected, setSelected] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  const [outfitId, setOutfitId] = useState<string | null>(null);
  const active = useRef(true);
  const { data: session } = useSession();
  const client = useQueryClient();
  const items = useItems({ is_archived: false }, page, 50);
  const create = useMutation({
    meta: { toastsOwnErrors: true },
    mutationFn: () => {
      applySessionToken(session);
      const previous = saved.get([...selected].sort().join(','));
      if (previous) return api.get<Outfit>(`/outfits/${previous}`);
      return api.post<Outfit>(`/considering/${itemId}/outfits`, { companion_ids: selected });
    },
    onSuccess: (outfit) => {
      saved.set([...selected].sort().join(','), outfit.id);
      client.setQueryData(queryKeys.outfit(outfit.id), outfit);
      client.invalidateQueries({ queryKey: queryKeys.outfits.all });
      if (active.current) setOutfitId(outfit.id);
    },
  });
  const available = items.data?.items.filter(item => item.status === 'ready') ?? [];
  return <div className="space-y-4">
    {outfitId ? <>
      <OutfitPreview outfitId={outfitId} cardImageIsPreview={false} autoDraw />
      <Link className="block underline" href={`/dashboard/outfits/${outfitId}`}>{t('savedPreview')}</Link>
      <Button variant="outline" onClick={() => { setOutfitId(null); create.reset(); }}>{t('chooseAgain')}</Button>
    </> : <>
      <p className="text-sm text-muted-foreground">{t('companions')}</p>
      {items.isLoading && <p role="status">{common('loading')}</p>}
      {items.isError && <p role="alert">{t('loadFailed')}</p>}
      {!items.isLoading && !items.isError && !available.length && <p>{t('emptyPicker')}</p>}
      <fieldset disabled={create.isPending} className="max-h-64 space-y-2 overflow-y-auto">
        {available.map(item => <label key={item.id} className="flex items-center gap-2 rounded border p-2">
          <input type="checkbox" checked={selected.includes(item.id)} disabled={!selected.includes(item.id) && selected.length >= 19}
            onChange={(event) => { create.reset(); setSelected(current => event.target.checked ? [...current, item.id] : current.filter(id => id !== item.id)); }} />
          {item.name || t('unnamed')}
        </label>)}
      </fieldset>
      <div className="flex gap-2">
        <Button variant="outline" disabled={page === 1 || create.isPending} onClick={() => setPage(current => current - 1)}>{common('previous')}</Button>
        <Button variant="outline" disabled={!items.data?.has_more || create.isPending} onClick={() => setPage(current => current + 1)}>{common('next')}</Button>
      </div>
      {create.isError && <p role="alert">{t('failed')}</p>}
      <Button disabled={create.isPending} onClick={() => create.mutate()}>{create.isPending ? common('loading') : t(selected.length ? 'previewSelected' : 'previewAlone')}</Button>
    </>}
    <Button variant="outline" onClick={() => { active.current = false; close(); }}>{common('cancel')}</Button>
  </div>;
}
