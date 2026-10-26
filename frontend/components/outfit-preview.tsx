'use client';

import { useState, type ReactNode } from 'react';
import { ThumbsUp } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

export function OutfitPreview(props: { outfitId: string; children?: ReactNode; detail?: boolean }) {
  return <PreviewContent key={props.outfitId} {...props} />;
}

function PreviewContent({ outfitId, children, detail }: { outfitId: string; children?: ReactNode; detail?: boolean }) {
  const t = useTranslations('outfits.preview');
  const { preview, pending, error, conflict, missingPhoto, likeConflict, draw, remove, like } = useOutfitPreview(outfitId);
  const [action, setAction] = useState<'redraw' | 'delete' | 'like' | null>(null);
  const [note, setNote] = useState('');
  const tc = useTranslations('common');
  return <div className={children && preview ? (detail ? "grid items-start gap-4 sm:grid-cols-2" : "grid grid-cols-2 gap-3 items-start") : "space-y-3"}>
    {children}
    <div className="space-y-2 min-w-0">
    {!preview && <Button variant="outline" disabled={pending} onClick={() => draw()}>{pending ? t('drawing') : t('see')}</Button>}
    {pending && <p role="status" className="text-sm text-muted-foreground">{t('drawing')}</p>}
    {missingPhoto && <Link className="block text-sm underline" href="/dashboard/settings#preview-photo">{t('addPhoto')}</Link>}
    {error && <p role="alert" className="text-sm text-destructive">{t('failed')}</p>}
    {preview && <>
      <a href={preview.image_url} target="_blank" rel="noopener noreferrer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={preview.medium_url || preview.image_url} alt={t('see')} className="max-h-96 w-full rounded-lg object-contain bg-muted" />
      </a>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {preview.stale && (preview.stale_reasons?.length ? preview.stale_reasons : ['unknown'] as const).map((reason) => <span key={reason}>{t(reason === 'clothes' ? 'clothesChanged' : reason === 'person' ? 'personChanged' : 'changed')}</span>)}
        <Button size="sm" variant="outline" disabled={pending} onClick={() => { setNote(''); setAction('redraw'); }}>{t('redraw')}</Button>
        <Button size="sm" variant={preview.liked ? 'default' : 'outline'} aria-pressed={Boolean(preview.liked)} disabled={pending} onClick={() => { setNote(preview.like_note ?? ''); setAction('like'); }}><ThumbsUp className="mr-1 h-4 w-4" />{t(preview.liked ? 'liked' : 'like')}</Button>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => setAction('delete')}>{t('delete')}</Button>
      </div>
    </>}
    </div>
    <Dialog open={action !== null} onOpenChange={(open) => { if (!open) setAction(null); }}>
      <DialogContent aria-describedby={undefined} className="max-w-sm">
        <DialogTitle>{t(action === 'delete' ? 'deleteConfirm' : action === 'like' ? 'like' : 'redraw')}</DialogTitle>
        {(action === 'redraw' || action === 'like') && <label className="space-y-2">
          <span>{t(action === 'like' ? 'likeNote' : 'changeNote')}</span>
          <Textarea aria-label={t(action === 'like' ? 'likeNote' : 'changeNote')} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>}
        {action === 'like' && likeConflict && <p role="alert" className="text-sm text-destructive">{t('likeChanged')}</p>}
        {action === 'redraw' && conflict && <p role="alert" className="text-sm text-destructive">{t('redrawConflict')}</p>}
        {action === 'redraw' && error && <p role="alert" className="text-sm text-destructive">{t('failed')}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setAction(null)}>{tc('cancel')}</Button>
          <Button disabled={pending} onClick={async () => {
            const selected = action;
            setAction(null);
            if (selected === 'delete') void remove();
            else if (selected === 'like') {
              if (preview && !await like(note, preview.created_at)) setAction('like');
            } else {
              const success = await draw(true, note);
              if (!success && note.trim()) setAction('redraw');
            }
          }}>{t(action === 'delete' ? 'delete' : action === 'like' ? 'like' : 'redraw')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  </div>;
}
