'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { api } from '@/lib/api';
import { validateCapture, type ProductCapture } from '@/lib/extension-reader/capture';
import { confirmedFilePayload, confirmedPayload } from '@/lib/extension-reader/confirmation';
import { localProductImport } from '@/lib/local-product-import';
import { useUserProfile } from '@/lib/hooks/use-user';
import { useSetTokenIfAvailable } from '@/lib/hooks/use-session-token';
import { ConsideringImport, type ImportTransport } from './considering-import';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';

export function ConsideringFallback() {
  const translate = useTranslations('wardrobe.fallback');
  const importText = useTranslations('wardrobe.import');
  const common = useTranslations('common');
  useSetTokenIfAvailable();
  const { data: user } = useUserProfile();
  const owner = useRef(user?.id); owner.current = user?.id;
  const generation = useRef(0);
  const preview = useRef<string>();
  const [link, setLink] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [transport, setTransport] = useState<ImportTransport>();
  const reset = useCallback(() => {
    generation.current++; if (preview.current) URL.revokeObjectURL(preview.current);
    preview.current = undefined; setTransport(undefined); setLink(''); setError(''); setBusy(false);
  }, []);
  useEffect(() => {
    generation.current++; setTransport(undefined); setBusy(false); setError('');
    if (user?.id) {
      try {
        const saved = sessionStorage.getItem(`considering-import:${user.id}`);
        if (saved) {
          const draft = JSON.parse(saved);
          if (typeof draft?.fileBacked !== 'boolean') throw Error('draft');
          if (draft.fileBacked) confirmedFilePayload(draft.capture); else confirmedPayload(draft.capture);
          setTransport(localProductImport(user.id, draft.capture, undefined, undefined, reset, () => owner.current));
        }
      } catch { setError('recoveryFailed'); }
    }
    return () => { generation.current++; if (preview.current) URL.revokeObjectURL(preview.current); preview.current = undefined; };
  }, [user?.id, reset]);
  function open(capture: ProductCapture, file?: File) {
    if (!user) throw Error('identity');
    if (file) preview.current = URL.createObjectURL(file);
    setTransport(localProductImport(user.id, capture, file, preview.current, reset, () => owner.current));
  }
  async function readLink() {
    const request = ++generation.current;
    setError('');
    try {
      const url = new URL(link);
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.href.length > 2000) throw Error('invalid');
      setBusy(true);
      const result = await api.post('/considering/extract', { url: url.href });
      if (request !== generation.current) return;
      const capture = validateCapture(result);
      if (!capture.images.length) throw Error('empty');
      open(capture);
    } catch { if (request === generation.current) setError('linkFailed'); }
    finally { if (request === generation.current) setBusy(false); }
  }
  async function choose(file?: File) {
    if (error === 'recoveryFailed') return;
    const request = ++generation.current;
    setError(''); setBusy(false);
    try {
      if (!file || !file.size || file.size > 20 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw Error('image');
      const bitmap = await createImageBitmap(file);
      const pixels = bitmap.width * bitmap.height; bitmap.close();
      if (pixels > 50000000) throw Error('image');
      if (request !== generation.current) return;
      const capture = { source_url: '', images: [], sizes: [], colors: [] };
      confirmedFilePayload(capture); open(capture, file);
    } catch { if (request === generation.current) setError('imageFailed'); }
  }
  if (transport) return <ConsideringImport transport={transport} />;
  return <main className="mx-auto max-w-3xl space-y-5 p-4">
    <h1 className="text-2xl font-bold">{translate('title')}</h1>
    <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (!busy) void readLink(); }}>
      <Label htmlFor="product-link">{translate('link')}</Label>
      <Input id="product-link" value={link} maxLength={2000} onChange={event => { generation.current++; setBusy(false); setLink(event.target.value); }} />
      <Button disabled={busy || !user || error === 'recoveryFailed'} type="submit">{busy ? common('loading') : translate('readLink')}</Button>
      <Button type="button" variant="outline" disabled={error === 'recoveryFailed'} onClick={reset}>{common('cancel')}</Button>
    </form>
    <p className="text-sm text-muted-foreground">{translate('publicOnly')}</p>
    {error && <p role="alert">{translate(error)}</p>}
    {error === 'recoveryFailed' && <Button type="button" variant="outline" onClick={() => { if (owner.current) sessionStorage.removeItem(`considering-import:${owner.current}`); reset(); }}>{importText('discardChecked')}</Button>}
    <div className="rounded border border-dashed p-6" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); void choose(event.dataTransfer.files.length === 1 ? event.dataTransfer.files[0] : undefined); }}>
      <Label htmlFor="product-image">{translate('imageDrop')}</Label>
      <Input id="product-image" disabled={error === 'recoveryFailed'} type="file" accept="image/jpeg,image/png,image/webp" onChange={event => { void choose(event.target.files?.[0]); event.target.value = ''; }} />
    </div>
  </main>;
}
