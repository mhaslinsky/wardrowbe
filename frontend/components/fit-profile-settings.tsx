'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { useFitProfile, useUpdateFitProfile, type FitProfile } from '@/lib/hooks/use-fit-profile';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

export function FitProfileSettings() {
  const t = useTranslations('settings.fitProfile');
  const common = useTranslations('common');
  const [draft, setDraft] = useState<FitProfile | null>(null);
  const [dirty, setDirty] = useState(false);
  const query = useFitProfile();
  const update = useUpdateFitProfile();
  const pending = query.isFetching || update.isPending;
  const [error, setError] = useState<'failed' | 'conflict' | null>(null);
  useEffect(() => {
    if (query.data && !dirty) setDraft(query.data);
  }, [query.data, dirty]);
  const load = async () => {
    const result = await query.refetch();
    if (result.isSuccess) {
      setDraft(result.data); setDirty(false); setError(null);
    }
  };
  const notice = error ?? (query.isError ? 'failed' : null);
  function edit(profile: FitProfile) { setDraft(profile); setDirty(true); setError(null); }
  return <Card id="fit-profile">
    <CardHeader><CardTitle>{t('title')}</CardTitle></CardHeader>
    <CardContent>
      <form className="space-y-4" onSubmit={async (event) => {
        event.preventDefault();
        if (!draft) return;
        try {
          setDraft(await update.mutateAsync(draft));
          setDirty(false); setError(null);
        } catch (failure) {
          setError((failure as { status?: number }).status === 409 ? 'conflict' : 'failed');
        }
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
          {notice && <div className="space-y-2">
            <p role="alert">{t(notice)}</p>
            <Button type="button" variant="outline" onClick={() => void load()}>{t('reload')}</Button>
          </div>}
        </fieldset>
      </form>
    </CardContent>
  </Card>;
}
