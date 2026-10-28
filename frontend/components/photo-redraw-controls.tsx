'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Sparkles } from 'lucide-react';
import { ApiError } from '@/lib/api';
import { Item } from '@/lib/types';
import { useFeatures } from '@/lib/hooks/use-features';
import { useChoosePhoto, useItem, useRedrawPhoto } from '@/lib/hooks/use-items';
import { Button } from '@/components/ui/button';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';

export function RedrawConfirmation({ open, onOpenChange, onConfirm, paidRetry, count = 1, onRetryChange, title, description }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  paidRetry: boolean;
  count?: number;
  title?: string;
  description?: string;
  onRetryChange?: (retry: boolean) => void;
}) {
  const translate = useTranslations('wardrobe.photoRedraw');
  const translateCommon = useTranslations('common');
  return <AlertDialog open={open} onOpenChange={onOpenChange}>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{title ?? translate('confirmTitle', { count })}</AlertDialogTitle>
        <AlertDialogDescription>{description ?? translate(paidRetry ? 'paidRetry' : 'paidConfirm')}</AlertDialogDescription>
      </AlertDialogHeader>
      {onRetryChange && <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={paidRetry} onChange={(event) => onRetryChange(event.target.checked)} />{translate('includeRetries')}
      </label>}
      <AlertDialogFooter>
        <AlertDialogCancel>{translateCommon('cancel')}</AlertDialogCancel>
        <AlertDialogAction onClick={onConfirm}>{translateCommon('confirm')}</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}

export function PhotoRedrawControls({ item }: { item: Item }) {
  const translate = useTranslations('wardrobe.photoRedraw');
  const { data: features } = useFeatures();
  const redraw = useRedrawPhoto();
  const choose = useChoosePhoto();
  const { refetch } = useItem(item.id);
  const [confirm, setConfirm] = useState(false);
  const [message, setMessage] = useState<'error' | 'conflict' | null>(null);
  const photo = item.photo_redraw;
  const status = photo?.status === 'generating' && photo.claim_stale === true ? 'failed' : photo?.status;
  const busy = redraw.isPending || choose.isPending || status === 'generating';
  const canRedraw = features?.photo_redraw === true && item.status === 'ready' && (!status || status === 'failed');
  const paidRetry = !!(photo?.paid || (photo?.provider_calls ?? 0) > 0);
  const choices = item.photo_choices;
  useEffect(() => { setConfirm(false); setMessage(null); }, [item.id]);

  async function act(action: () => Promise<unknown>) {
    setMessage(null);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof ApiError && error.status === 409 ? 'conflict' : 'error');
      await refetch();
    }
  }
  const statusKey = status === 'generating' || status === 'skipped' || status === 'color_drift' || status === 'source_changed' || status === 'failed' ? status : null;
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      {canRedraw && <Button variant="outline" size="sm" disabled={busy} onClick={() => setConfirm(true)}>
        <Sparkles className="mr-2 h-4 w-4" />{translate(status === 'failed' ? 'retry' : 'redraw')}
      </Button>}
      {photo?.selected === 'redrawn' && choices?.original && <Button variant="secondary" size="sm" disabled={busy}
        onClick={() => void act(() => choose.mutateAsync({ id: item.id, choice: 'original' }))}>{translate('redrawn')}</Button>}
      {choices?.original && photo?.selected !== 'original' && photo?.selected !== 'redrawn' && <Button variant="outline" size="sm" disabled={busy}
        onClick={() => void act(() => choose.mutateAsync({ id: item.id, choice: 'original' }))}>{translate('useOriginal')}</Button>}
      {choices?.redrawn && photo?.selected !== 'redrawn' && <Button variant="outline" size="sm" disabled={busy}
        onClick={() => void act(() => choose.mutateAsync({ id: item.id, choice: 'redrawn' }))}>{translate('useRedrawn')}</Button>}
    </div>
    {photo?.selected === 'redrawn' && <p className="text-xs text-muted-foreground">{translate('switchBack')}</p>}
    {statusKey && <p role="status" className="text-sm text-muted-foreground">{translate(statusKey)}</p>}
    {message && <p role="alert" className="text-sm text-destructive">{translate(message)}</p>}
    <RedrawConfirmation open={confirm} onOpenChange={setConfirm} paidRetry={paidRetry}
      onConfirm={() => { setConfirm(false); void act(() => redraw.mutateAsync({ id: item.id, retry: paidRetry })); }} />
  </div>;
}
