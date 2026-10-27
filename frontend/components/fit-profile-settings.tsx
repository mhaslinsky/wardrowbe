'use client';

import { useCallback, useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { z } from 'zod';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

const text = z.string();
const measurement = z.number().finite().positive().max(300).nullable();
const profileSchema = z.object({
  rev: text.min(1).max(64),
  brand_sizes: z.array(z.object({ brand: text, line: text, size: text, note: text })),
  skip_list: z.array(z.object({ brand: text, line: text, reason: text })),
  neck: measurement,
  sleeve: measurement,
  thigh: measurement,
  reference_garment: text,
});
type FitProfile = z.infer<typeof profileSchema>;
const endpoint = '/users/me/preferences/fit-profile';

export function FitProfileSettings() {
  const t = useTranslations('settings.fitProfile');
  const common = useTranslations('common');
  const [draft, setDraft] = useState<FitProfile | null>(null);
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<'failed' | 'conflict' | null>(null);
  const load = useCallback(async () => {
    setPending(true);
    try {
      setDraft(profileSchema.parse(await api.get<unknown>(endpoint)));
      setDirty(false); setError(null);
    } catch { setError('failed'); }
    finally { setPending(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  function edit(profile: FitProfile) { setDraft(profile); setDirty(true); setError(null); }
  return <Card id="fit-profile">
    <CardHeader><CardTitle>{t('title')}</CardTitle></CardHeader>
    <CardContent>
      <form className="space-y-4" onSubmit={async (event) => {
        event.preventDefault();
        if (!draft) return;
        setPending(true);
        try {
          setDraft(profileSchema.parse(await api.patch<unknown>(endpoint, draft)));
          setDirty(false); setError(null);
        } catch (failure) {
          setError((failure as { status?: number }).status === 409 ? 'conflict' : 'failed');
        } finally { setPending(false); }
      }}>
        <fieldset disabled={pending} className="space-y-4">
          {draft && <>
            <fieldset className="space-y-3">
              <legend className="font-medium">{t('brandSizes')}</legend>
              {draft.brand_sizes.map((row, index) => <div key={index} className="grid gap-2 rounded border p-3 sm:grid-cols-2">
                {(['brand', 'line', 'size', 'note'] as const).map((field) => <label key={field}>
                  <span className="text-sm">{t(field)}</span>
                  <Input required={field === 'brand' || field === 'size'} maxLength={300} value={row[field]} onChange={(event) => edit({ ...draft, brand_sizes: draft.brand_sizes.map((entry, position) => position === index ? { ...entry, [field]: event.target.value } : entry) })} />
                </label>)}
                <Button type="button" variant="outline" onClick={() => edit({ ...draft, brand_sizes: draft.brand_sizes.filter((_, position) => position !== index) })}>{common('remove')}</Button>
              </div>)}
              <Button type="button" variant="outline" disabled={draft.brand_sizes.length >= 100} onClick={() => edit({ ...draft, brand_sizes: [...draft.brand_sizes, { brand: '', line: '', size: '', note: '' }] })}>{t('addBrand')}</Button>
            </fieldset>
            <fieldset className="space-y-3">
              <legend className="font-medium">{t('skipList')}</legend>
              {draft.skip_list.map((row, index) => <div key={index} className="grid gap-2 rounded border p-3 sm:grid-cols-2">
                {(['brand', 'line', 'reason'] as const).map((field) => <label key={field}>
                  <span className="text-sm">{t(field)}</span>
                  <Input required={field === 'reason' || (field === 'brand' && !row.line.trim())} maxLength={300} value={row[field]} onChange={(event) => edit({ ...draft, skip_list: draft.skip_list.map((entry, position) => position === index ? { ...entry, [field]: event.target.value } : entry) })} />
                </label>)}
                <Button type="button" variant="outline" onClick={() => edit({ ...draft, skip_list: draft.skip_list.filter((_, position) => position !== index) })}>{common('remove')}</Button>
              </div>)}
              <Button type="button" variant="outline" disabled={draft.skip_list.length >= 100} onClick={() => edit({ ...draft, skip_list: [...draft.skip_list, { brand: '', line: '', reason: '' }] })}>{t('addSkip')}</Button>
            </fieldset>
            <fieldset className="grid gap-3 sm:grid-cols-3">
              <legend className="font-medium">{t('measurements')}</legend>
              {(['neck', 'sleeve', 'thigh'] as const).map((field) => <label key={field}>
                <span className="text-sm">{t(field)}</span>
                <Input type="number" min="0.1" max="300" step="0.1" value={draft[field] ?? ''} onChange={(event) => edit({ ...draft, [field]: event.target.value === '' ? null : Number(event.target.value) })} />
              </label>)}
            </fieldset>
            <label className="block">
              <span className="text-sm">{t('referenceGarment')}</span>
              <textarea className="block w-full rounded border p-2" maxLength={1000} value={draft.reference_garment} onChange={(event) => edit({ ...draft, reference_garment: event.target.value })} />
            </label>
            <Button type="submit" disabled={!dirty}>{common('save')}</Button>
          </>}
          {pending && <p role="status">{common('loading')}</p>}
          {error && <div className="space-y-2">
            <p role="alert">{t(error)}</p>
            <Button type="button" variant="outline" onClick={() => void load()}>{t('reload')}</Button>
          </div>}
        </fieldset>
      </form>
    </CardContent>
  </Card>;
}
