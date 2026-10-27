'use client';

import { useRef, useState } from 'react';
import Link from 'next/link';
import * as Dialog from '@radix-ui/react-dialog';
import { useTranslations } from 'next-intl';
import { ApiError } from '@/lib/api';
import { useConsidering, type ConsideringEntry } from '@/lib/hooks/use-considering';
import { garmentImageUrl } from '@/lib/garment-image';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ConsideringSuggestionsAction } from '@/components/considering-suggestions-action';
import { ConsideringPreviewAction } from '@/components/considering-preview-action';
import { Label } from '@/components/ui/label';

export default function ConsideringPage() {
  const t = useTranslations('wardrobe.considering');
  const common = useTranslations('common');
  const fallback = useTranslations('wardrobe.fallback');
  const dialogOpener = useRef<HTMLButtonElement | null>(null);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const [state, setState] = useState<'considering' | 'passed'>('considering');
  const { shelf, change } = useConsidering(state);
  const [dialog, setDialog] = useState<{ entry: ConsideringEntry; kind: 'edit' | 'passed' } | null>(null);
  const [name, setName] = useState('');
  const [price, setPrice] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [size, setSize] = useState('');
  const [reason, setReason] = useState('');
  const [validation, setValidation] = useState<'invalid' | 'reasonRequired' | null>(null);
  const [boughtId, setBoughtId] = useState<string | null>(null);
  const busy = change.isPending;
  const error = validation || (change.error instanceof ApiError && change.error.status === 409 ? 'conflict' : change.error ? 'failed' : null);
  function resetErrors() { change.reset(); setValidation(null); }
  function openDialog(entry: ConsideringEntry, kind: 'edit' | 'passed', opener: HTMLButtonElement) {
    dialogOpener.current = opener;
    resetErrors(); setBoughtId(null);
    setName(entry.item.name || ''); setPrice(entry.price ?? '');
    setCurrency(entry.currency); setSize(entry.planned_size || ''); setReason('');
    setDialog({ entry, kind });
  }
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4">
      <h1 ref={heading} tabIndex={-1} className="text-2xl font-bold">{t('title')}</h1>
      <Link className="inline-block rounded border px-4 py-2" href="/dashboard/considering/import">{fallback('title')}</Link>
      <div className="flex gap-2" role="group" aria-label={t('title')}>
        {(['considering', 'passed'] as const).map((tab) => <Button key={tab} disabled={busy}
          variant={state === tab ? 'default' : 'outline'} aria-pressed={state === tab}
          onClick={() => { resetErrors(); setBoughtId(null); setState(tab); }}>
          {tab === 'considering' ? t('title') : t('passed')}
        </Button>)}
      </div>
      {error && !dialog && <p role="alert">{t(error)}</p>}
      {boughtId && <p role="status"><Link className="underline" href={`/dashboard/wardrobe?item=${boughtId}`}>{t('inWardrobe')}</Link></p>}
      {shelf.isLoading && <p role="status">{common('loading')}</p>}
      {shelf.isError && <p role="alert">{t('loadFailed')}</p>}
      {!shelf.isError && shelf.data?.length === 0 && <p className="rounded-lg border p-8 text-center text-muted-foreground">{state === 'considering' ? t('empty') : t('emptyPassed')}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {shelf.data?.map((entry) => {
          const image = garmentImageUrl(entry.item, 'card');
          const source = entry.source_url && /^https?:\/\//i.test(entry.source_url) ? entry.source_url : null;
          return <section key={entry.item.id} className="overflow-hidden rounded-lg border bg-card">
            {image && <img className="aspect-square w-full bg-muted object-contain" src={image} alt={entry.item.name || t('unnamed')} />}
            <div className="space-y-3 p-4">
              <div><h2 className="text-lg font-semibold">{entry.item.name || t('unnamed')}</h2>
                {entry.item.brand && <p className="text-sm text-muted-foreground">{entry.item.brand}</p>}</div>
              {entry.price !== null && <p>{entry.currency} {Number(entry.price).toFixed(2)}</p>}
              {entry.planned_size && <p><span className="text-muted-foreground">{t('size')}: </span>{entry.planned_size}</p>}
              {source && <a className="block underline" href={source} target="_blank" rel="noopener noreferrer">{t('source')}</a>}
              {state === 'passed' && <p className="whitespace-pre-wrap break-words text-sm">{entry.passed_reason}</p>}
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" disabled={busy} onClick={(event) => openDialog(entry, 'edit', event.currentTarget)}>{common('edit')}</Button>
                {state === 'considering' ? <>
                  <ConsideringSuggestionsAction key={`${entry.item.id}:${entry.item.status}:${entry.item.is_archived}`} itemId={entry.item.id} disabled={busy || entry.item.status !== 'ready'} />
                  <ConsideringPreviewAction itemId={entry.item.id} disabled={busy || entry.item.status !== 'ready'} />
                  <Button disabled={busy} onClick={() => {
                    if (busy || !confirm(t('buyConfirm'))) return;
                    resetErrors(); setBoughtId(null);
                    change.mutate({ id: entry.item.id, action: 'bought' }, { onSuccess: () => setBoughtId(entry.item.id) });
                  }}>{t('bought')}</Button>
                  <Button variant="outline" disabled={busy} onClick={(event) => openDialog(entry, 'passed', event.currentTarget)}>{t('pass')}</Button>
                </> : <Button disabled={busy} onClick={() => {
                  resetErrors(); change.mutate({ id: entry.item.id, action: 'restore' });
                }}>{t('restore')}</Button>}
              </div>
            </div>
          </section>;
        })}
      </div>
      <Dialog.Root open={!!dialog} onOpenChange={(open) => { if (!open && !busy) { setDialog(null); resetErrors(); } }}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/60" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border bg-background p-6 shadow-lg" aria-describedby={undefined} onCloseAutoFocus={(event) => {
            event.preventDefault();
            const opener = dialogOpener.current;
            if (opener?.isConnected && !opener.disabled) opener.focus();
            else heading.current?.focus();
          }}>
            <Dialog.Title className="mb-4 text-lg font-semibold">{dialog?.kind === 'edit' ? common('edit') : t('pass')}</Dialog.Title>
            <form className="space-y-4" onSubmit={(event) => {
              event.preventDefault();
              if (!dialog || busy) return;
              resetErrors();
              if (dialog.kind === 'passed') {
                if (!reason.trim()) { setValidation('reasonRequired'); return; }
                change.mutate({ id: dialog.entry.item.id, action: 'passed', reason: reason.trim() }, { onSuccess: () => setDialog(null) });
              } else {
                if (!/^[A-Z]{3}$/.test(currency) || (price !== '' && (!/^\d+(\.\d{1,2})?$/.test(price) || Number(price) > 99999999.99))) {
                  setValidation('invalid'); return;
                }
                change.mutate({ id: dialog.entry.item.id, action: 'edit', fields: {
                  name: name.trim() || null, price: price === '' ? null : price, currency, planned_size: size.trim() || null,
                } }, { onSuccess: () => setDialog(null) });
              }
            }}>
              <fieldset disabled={busy} className="space-y-4" onChange={() => setValidation(null)}>
                {dialog?.kind === 'passed' ? <div>
                  <Label htmlFor="pass-reason">{t('reason')}</Label>
                  <textarea id="pass-reason" required maxLength={2000} className="min-h-24 w-full rounded-md border bg-background p-2" value={reason} onChange={(event) => setReason(event.target.value)} />
                </div> : <>
                  <div><Label htmlFor="shelf-name">{t('name')}</Label><Input id="shelf-name" maxLength={100} value={name} onChange={(event) => setName(event.target.value)} /></div>
                  <div className="grid grid-cols-2 gap-4">
                    <div><Label htmlFor="shelf-price">{t('price')}</Label><Input id="shelf-price" inputMode="decimal" value={price} onChange={(event) => setPrice(event.target.value)} /></div>
                    <div><Label htmlFor="shelf-currency">{t('currency')}</Label><Input id="shelf-currency" required maxLength={3} value={currency} onChange={(event) => setCurrency(event.target.value.toUpperCase())} /></div>
                  </div>
                  <div><Label htmlFor="shelf-size">{t('size')}</Label><Input id="shelf-size" maxLength={100} value={size} onChange={(event) => setSize(event.target.value)} /></div>
                </>}
                {error && <p role="alert">{t(error)}</p>}
                <div className="flex justify-end gap-2">
                  <Button type="button" variant="outline" onClick={() => { setDialog(null); resetErrors(); }}>{common('cancel')}</Button>
                  <Button type="submit">{common('save')}</Button>
                </div>
              </fieldset>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
