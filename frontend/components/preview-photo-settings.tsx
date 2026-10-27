'use client';

import { useEffect, useRef, useState } from 'react';
import { ACCEPTED_IMAGE_INPUT } from '@/lib/image-types';
import { useTranslations } from 'next-intl';
import { usePreferences, useUpdatePreferences } from '@/lib/hooks/use-preferences';
import type { Preferences } from '@/lib/types';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '@/components/ui/card';
import { previewAngles, type PreviewAngle, usePreviewPhoto } from '@/lib/hooks/use-outfit-preview';

export function PreviewPhotoSettings() {
  const t = useTranslations('settings.previewPhoto');
  const common = useTranslations('common');
  const { photos, loading, pending, error, save } = usePreviewPhoto();
  const [confirm, setConfirm] = useState<PreviewAngle | null>(null);
  const [open, setOpen] = useState(false);
  const [scrollPending, setScrollPending] = useState(false);
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const openFromHash = () => {
      if (window.location.hash === '#preview-photo') {
        setOpen(true);
        setScrollPending(true);
      }
    };
    openFromHash();
    window.addEventListener('hashchange', openFromHash);
    return () => window.removeEventListener('hashchange', openFromHash);
  }, []);
  useEffect(() => {
    if (!open || !scrollPending || loading) return;
    const images = Array.from(card.current?.querySelectorAll('img') ?? []);
    const alignCard = () => {
      card.current?.scrollIntoView({ block: 'start' });
      if (images.every((image) => image.complete)) setScrollPending(false);
    };
    for (const image of images) {
      image.addEventListener('load', alignCard);
      image.addEventListener('error', alignCard);
    }
    alignCard();
    return () => {
      for (const image of images) {
        image.removeEventListener('load', alignCard);
        image.removeEventListener('error', alignCard);
      }
    };
  }, [open, scrollPending, loading, photos]);
  const angleCount = previewAngles.filter((angle) => photos?.[angle]).length;
  return <Card id="preview-photo" ref={card}>
    <Collapsible open={open} onOpenChange={setOpen}>
    <CardHeader>
      <CardTitle>{t('title')}</CardTitle>
      <CardDescription>{loading ? common('loading') : error ? t('failed') : t('anglesSet', { count: angleCount })}</CardDescription>
      <CollapsibleTrigger asChild>
        <Button variant="outline" aria-expanded={open} aria-controls="preview-photo-content">{t(open ? 'hidePhotos' : 'showPhotos')}</Button>
      </CollapsibleTrigger>
    </CardHeader>
    <CardContent>
      <PreviewPreferencesEditor />
    </CardContent>
    <CollapsibleContent>
    <CardContent id="preview-photo-content" className="space-y-3">
      <CardDescription>{t('guidance')}</CardDescription>
      <div className="grid gap-6 sm:grid-cols-3">
        {previewAngles.map((angle) => {
          const photo = photos?.[angle];
          const label = t(photo ? 'replaceAngle' : 'uploadAngle', { angle: t(angle) });
          return <fieldset key={angle} className="min-w-0 space-y-3">
            <legend className="font-medium">{t(angle)}</legend>
            {photo && <a href={photo.image_url} target="_blank" rel="noopener noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={photo.medium_url || photo.image_url} alt={t(angle === 'front' ? 'title' : angle)} className="max-h-64 rounded-lg object-contain" />
            </a>}
            <label className="block space-y-2 text-sm font-medium">
              <span>{label}</span>
              <input type="file" accept={ACCEPTED_IMAGE_INPUT} disabled={loading || pending}
                aria-label={label}
                className="block w-full text-sm" onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (file) { setConfirm(null); void save(file, angle); }
                }} />
            </label>
            {photo && confirm !== angle && <Button variant="outline" disabled={pending} onClick={() => setConfirm(angle)}>{common('remove')}</Button>}
            {confirm === angle && <div className="space-y-2">
              <p>{t(angle === 'front' ? 'confirm' : 'confirmAngle')}</p>
              <div className="flex gap-2">
                <Button variant="destructive" disabled={pending} onClick={async () => { if (await save(undefined, angle)) setConfirm(null); }}>{common('remove')}</Button>
                <Button variant="outline" disabled={pending} onClick={() => setConfirm(null)}>{common('cancel')}</Button>
              </div>
            </div>}
          </fieldset>;
        })}
      </div>
      {(loading || pending) && <p role="status">{common('loading')}</p>}
      {error && <p role="alert" className="text-destructive">{t('failed')}</p>}
    </CardContent>
    </CollapsibleContent>
    </Collapsible>
  </Card>;
}


function PreviewPreferencesEditor() {
  const t = useTranslations('settings.previewPhoto');
  const { data } = usePreferences();
  const update = useUpdatePreferences();
  const [draft, setDraft] = useState<Preferences['preview_preferences'] | null>(null);
  const [text, setText] = useState('');
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    if (data?.preview_preferences && !dirty) {
      setDraft(data.preview_preferences);
      setText(data.preview_preferences.lines.join('\n'));
    }
  }, [data, dirty]);
  if (!draft) return null;
  return <fieldset className="space-y-3" disabled={update.isPending}>
    <label className="block space-y-2">
      <span className="block">{t('shirts')}</span>
      <select className="block rounded border p-2" aria-label={t('shirts')} value={draft.tuck} onChange={(event) => {
        setDraft({ ...draft, tuck: event.target.value as typeof draft.tuck }); setDirty(true);
      }}>
        {(['outfit', 'untucked', 'tucked'] as const).map((value) => <option key={value} value={value}>{t(value)}</option>)}
      </select>
    </label>
    <label className="block space-y-2">
      <span>{t('preferences')}</span>
      <textarea aria-label={t('preferences')} className="block w-full rounded border p-2" rows={4} value={text}
        onChange={(event) => { setText(event.target.value); setDirty(true); }} />
    </label>
    <p className="text-sm">{t('limits')}</p>
    <Button disabled={!dirty} onClick={async () => {
      const lines = text.split('\n').map((line) => line.trim()).filter(Boolean);
      if (lines.length > 10 || lines.some((line) => line.length > 120)) { setError(true); return; }
      try {
        const saved = await update.mutateAsync({ preview_preferences: { ...draft, lines } });
        setDraft(saved.preview_preferences); setText(saved.preview_preferences.lines.join('\n'));
        setDirty(false); setError(false);
      } catch { setError(true); }
    }}>{t('savePreferences')}</Button>
    {error && <p role="alert">{t('saveConflict')}</p>}
  </fieldset>;
}
