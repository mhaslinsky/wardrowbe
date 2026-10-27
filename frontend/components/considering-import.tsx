'use client';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import { useTranslations } from 'next-intl';
import { useUserProfile } from '@/lib/hooks/use-user';
import { useConsideringImport, type ConfirmedProduct } from '@/lib/hooks/use-considering-import';
import { queryKeys } from '@/lib/hooks/query-keys';
import { MATERIAL_VALUES, CLOTHING_COLORS } from '@/lib/generated/garment-vocabulary';
import { confirmedPayload } from '../lib/extension-reader/confirmation';
import { validateCapture } from '../lib/extension-reader/capture';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export function ConsideringImport() {
  const t = useTranslations('wardrobe.import');
  const common = useTranslations('common');
  const { data: user } = useUserProfile();
  const bridge = useConsideringImport(user?.id);
  const [capture, setCapture] = useState<ConfirmedProduct>();
  const [images, setImages] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [partial, setPartial] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const client = useQueryClient();
  const router = useRouter();
  useEffect(() => {
    if (!user) return;
    let active = true;
    bridge('load').then(result => {
      const data = validateCapture(result.capture);
      if (active) {
        setCapture(data); setImages(data.images); setSelected(data.images.slice(0, 5)); setPartial(!!result.partial); setUncertain(!!result.uncertain);
      }
    }).catch(() => { if (active) setError('loadFailed'); });
    return () => { active = false; };
  }, [bridge, user]);
  const fields = { name: 100, brand: 100, price: 24, currency: 3, selected_size: 32, selected_color: 100,
    material_detail: 1000, care: 255, fit: 2000, size_chart: 6000 } as const;
  async function act(action: 'save' | 'cancel') {
    if (busy || !capture) return;
    setError('');
    if (action === 'save' && !partial) {
      try { confirmedPayload({ ...capture, images: selected }); } catch { setError('invalid'); return; }
    }
    setBusy(true);
    try {
      const result = await bridge(action, { ...capture, images: selected });
      if (action === 'save' && (!result.saved || typeof result.item !== 'string')) throw Error('response');
      await client.invalidateQueries({ queryKey: queryKeys.considering.list(user?.email, 'considering') });
      await client.invalidateQueries({ queryKey: queryKeys.items.all });
      router.push('/dashboard/considering');
    } catch (failure) {
      setError(failure instanceof Error && ['token', 'rate', 'identity', 'partial', 'signin', 'uncertain'].includes(failure.message) ? failure.message : 'saveFailed');
      if (action === 'save') {
        const result = await bridge('load').catch(() => undefined);
        if (result?.partial) setPartial(true);
        if (result?.uncertain) setUncertain(true);
      }
    } finally { setBusy(false); }
  }
  return <main className="mx-auto max-w-3xl space-y-5 p-4">
    <h1 className="text-2xl font-bold">{t('title')}</h1>
    {error && <p role="alert">{t(error)}</p>}
    {uncertain && <p role="alert">{t('uncertain')}</p>}
    {partial && <p role="status">{t('partial')}</p>}
    {!capture && !error && <p role="status">{common('loading')}</p>}
    {capture && <form className="space-y-4" onSubmit={event => { event.preventDefault(); void act('save'); }}>
      <fieldset disabled={busy || partial || uncertain} className="space-y-4">
        <p className="break-all text-sm">{capture.source_url}</p>
        {Object.entries(fields).map(([key, limit]) => <div key={key}>
          <Label htmlFor={`import-${key}`}>{t(key)}</Label>
          <Input id={`import-${key}`} maxLength={limit} value={capture[key as keyof typeof fields] || ''}
            onChange={event => setCapture({ ...capture, [key]: event.target.value || undefined })} />
        </div>)}
        <div><Label htmlFor="import-material">{t('material')}</Label><select id="import-material" className="block w-full rounded border bg-background p-2" value={capture.material || ''}
          onChange={event => setCapture({ ...capture, material: event.target.value })}>
          <option value="">{t('unknown')}</option>{MATERIAL_VALUES.map(material => <option key={material} value={material}>{material}</option>)}
        </select></div>
        <p>{t('variants')}: {capture.sizes.join(', ')}; {capture.colors.join(', ')}</p>
        <datalist id="import-colors">{CLOTHING_COLORS.map(color => <option key={color.value} value={color.value} />)}</datalist>
        <p>{t('images')}</p><div className="grid grid-cols-3 gap-3">{images.map((image, index) => <label key={image} className="rounded border p-2">
          <img src={image} alt={t('image', { number: index + 1 })} referrerPolicy="no-referrer" className="aspect-square w-full object-contain" />
          <input type="checkbox" checked={selected.includes(image)} aria-label={t('image', { number: index + 1 })}
            onChange={event => setSelected(event.target.checked ? [...selected, image] : selected.filter(value => value !== image))} />
        </label>)}</div>
      </fieldset>
      <div className="flex gap-2"><Button disabled={busy || uncertain || (selected.length === 0 && !partial)} type="submit">{partial ? t('retry') : common('save')}</Button>
        <Button disabled={busy || partial || uncertain} type="button" variant="outline" onClick={() => void act('cancel')}>{common('cancel')}</Button></div>
    </form>}
  </main>;
}
