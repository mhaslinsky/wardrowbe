'use client';

import { useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { ApiError } from '@/lib/api';
import { useConsideringSuggestions } from '@/lib/hooks/use-considering-suggestions';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { OutfitCard } from '@/components/outfits/outfit-card';
import { OutfitPreview } from '@/components/outfit-preview';

export function ConsideringSuggestionsAction({ itemId, disabled }: { itemId: string; disabled?: boolean }) {
  const t = useTranslations('wardrobe.considering');
  const [open, setOpen] = useState(false);
  return <>
    <Button variant="outline" disabled={disabled} onClick={() => setOpen(true)}>{t('wearWith')}</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent aria-describedby={undefined} className="max-h-[90dvh] max-w-4xl overflow-y-auto">
        <DialogTitle>{t('wearWith')}</DialogTitle>
        {open && <Suggestions itemId={itemId} close={() => setOpen(false)} />}
      </DialogContent>
    </Dialog>
  </>;
}

function Suggestions({ itemId, close }: { itemId: string; close: () => void }) {
  const t = useTranslations('wardrobe.considering');
  const common = useTranslations('common');
  const generation = useConsideringSuggestions(itemId);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const aiDisabled = generation.error instanceof ApiError && generation.error.message === 'Internal AI is disabled';
  return <div className="space-y-4">
    {generation.isPending && <p role="status">{common('loading')}</p>}
    {generation.isError && <p role="alert">{t(aiDisabled ? 'aiDisabled' : 'failed')}</p>}
    <div className="grid gap-4 sm:grid-cols-3">
      {generation.data?.map(outfit => <OutfitPreview key={outfit.id} outfitId={outfit.id}>
        <OutfitCard outfit={outfit} />
      </OutfitPreview>)}
    </div>
    <div className="flex gap-2">
      <Button disabled={generation.isPending || aiDisabled} onClick={() => {
        generation.reset();
        request.current = new AbortController();
        generation.mutate(request.current.signal);
      }}>{t(generation.data ? 'chooseAgain' : 'suggest')}</Button>
      <Button variant="outline" onClick={() => { request.current?.abort(); close(); }}>{common('cancel')}</Button>
    </div>
  </div>;
}
