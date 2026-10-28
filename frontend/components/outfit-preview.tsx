'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { MessageSquare } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { Button } from '@/components/ui/button';
import { RedrawConfirmation } from '@/components/photo-redraw-controls';
import { useOutfitPreview } from '@/lib/hooks/use-outfit-preview';

export function OutfitPreview(props: { outfitId: string; children?: ReactNode; detail?: boolean; autoDraw?: boolean; cardImageUrl?: string | null; cardImageIsPreview: boolean; hasRealPhoto?: boolean }) {
  return <PreviewContent key={`${props.outfitId}:${props.cardImageUrl ? props.cardImageIsPreview ? 'preview' : 'photo' : 'none'}`} {...props} />;
}

function PreviewContent({ outfitId, children, detail, autoDraw, cardImageUrl, cardImageIsPreview, hasRealPhoto }: { outfitId: string; children?: ReactNode; detail?: boolean; autoDraw?: boolean; cardImageUrl?: string | null; cardImageIsPreview: boolean; hasRealPhoto?: boolean }) {
  const t = useTranslations('outfits.preview');
  const { preview, pending, error, comparisonPending, comparisonError, conflict, missingPhoto, commentConflict, draw, remove, saveComment, compare } = useOutfitPreview(outfitId);
  const started = useRef(false);
  useEffect(() => {
    let disposed = false;
    queueMicrotask(() => { if (!disposed && autoDraw && !started.current) { started.current = true; void draw(); } });
    return () => { disposed = true; };
  }, [autoDraw, draw]);
  const [action, setAction] = useState<'redraw' | 'delete' | 'comment' | null>(null);
  const [note, setNote] = useState('');
  const [comparisonCallId, setComparisonCallId] = useState<string>();
  const [comparisonNotice, setComparisonNotice] = useState(false);
  const [compareConfirm, setCompareConfirm] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const collapsed = Boolean(cardImageUrl) && !expanded;
  const selectedPreview = Boolean(cardImageUrl && cardImageIsPreview);
  const tc = useTranslations('common');
  return <div className={children && preview && detail && !cardImageUrl ? "grid min-w-0 items-start gap-4 sm:grid-cols-2" : "min-w-0 space-y-3"}>
    {children}
    {cardImageUrl && <Button variant="outline" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{t(expanded ? 'hide' : preview ? 'show' : 'title')}</Button>}
    {preview && hasRealPhoto && <Button size="sm" variant="outline" disabled={pending} onClick={() => setCompareConfirm(true)}>{t('compare')}</Button>}
    {comparisonPending && <p role="status" className="text-sm text-muted-foreground">{t('comparing')}</p>}
    {comparisonError && <p role="alert" className="text-sm text-destructive">{t('comparisonFailed')}</p>}
    <div className="space-y-2 min-w-0" hidden={collapsed}>
    {!preview && <Button variant="outline" disabled={pending} onClick={() => draw()}>{pending ? t('drawing') : t('see')}</Button>}
    {pending && !comparisonPending && <p role="status" className="text-sm text-muted-foreground">{t('drawing')}</p>}
    {missingPhoto && <Link className="block text-sm underline" href="/dashboard/settings#preview-photo">{t('addPhoto')}</Link>}
    {error && <p role="alert" className="text-sm text-destructive">{t('failed')}</p>}
    {preview && <>
      {!selectedPreview && <a href={preview.image_url} target="_blank" rel="noopener noreferrer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={preview.medium_url || preview.image_url} alt={t('see')} className="max-h-96 w-full rounded-lg object-contain bg-muted" />
      </a>}
      {preview.stale && <div className="space-y-1 text-sm">
        {(preview.stale_reasons?.length ? preview.stale_reasons : ['unknown'] as const).map((reason) => <p key={reason}>{t(reason === 'clothes' ? 'clothesChanged' : reason === 'person' ? 'personChanged' : reason === 'preferences' ? 'preferencesChanged' : 'changed')}</p>)}
      </div>}
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Button size="sm" variant="outline" disabled={pending} onClick={() => { setNote(''); setComparisonCallId(undefined); setComparisonNotice(false); setAction('redraw'); }}>{t('redraw')}</Button>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => { setNote(preview.comment); setAction('comment'); }}><MessageSquare className="mr-1 h-4 w-4" />{t('comment')}</Button>
        <Button size="sm" variant="outline" disabled={pending} onClick={() => setAction('delete')}>{t('delete')}</Button>
      </div>
      {preview.comment && <p className="truncate text-sm text-muted-foreground" title={preview.comment}>{preview.comment}</p>}
    </>}
    </div>
    <RedrawConfirmation title={t('compare')} description={t('comparePaid')} open={compareConfirm} onOpenChange={setCompareConfirm} paidRetry={false} onConfirm={() => {
      setCompareConfirm(false); setComparisonNotice(false);
      void compare().then((result) => {
        if (result) { setNote(result.note); setComparisonCallId(result.call_id); setAction('redraw'); }
      });
    }} />
    <Dialog open={action !== null} onOpenChange={(open) => { if (!open) setAction(null); }}>
      <DialogContent aria-describedby={undefined} className="max-w-sm">
        <DialogTitle>{t(action === 'delete' ? 'deleteConfirm' : action === 'comment' ? 'comment' : 'redraw')}</DialogTitle>
        {(action === 'redraw' || action === 'comment') && <label className="space-y-2">
          <span>{t(action === 'comment' ? 'commentNote' : 'changeNote')}</span>
          <Textarea placeholder={action === 'comment' ? t('commentNotePlaceholder') : undefined} aria-label={t(action === 'comment' ? 'commentNote' : 'changeNote')} maxLength={500} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>}
        {action === 'comment' && commentConflict && <p role="alert" className="text-sm text-destructive">{t('commentChanged')}</p>}
        {action === 'redraw' && comparisonNotice && <p role="alert" className="text-sm text-destructive">{t('comparisonChanged')}</p>}
        {action === 'redraw' && conflict && <p role="alert" className="text-sm text-destructive">{t('redrawConflict')}</p>}
        {action === 'redraw' && error && <p role="alert" className="text-sm text-destructive">{t('failed')}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setAction(null)}>{tc('cancel')}</Button>
          <Button disabled={pending} onClick={async () => {
            const selected = action;
            setAction(null);
            if (selected === 'delete') void remove();
            else if (selected === 'comment') {
              if (preview && !await saveComment(note, preview.created_at)) setAction('comment');
            } else {
              const success = await draw(true, note, note.trim() ? comparisonCallId : undefined);
              if (!success && comparisonCallId) { setComparisonCallId(undefined); setComparisonNotice(true); }
              if (!success && note.trim()) setAction('redraw');
            }
          }}>{action === 'comment' ? tc('save') : t(action === 'delete' ? 'delete' : 'redraw')}</Button>
        </div>
      </DialogContent>
    </Dialog>
  </div>;
}
