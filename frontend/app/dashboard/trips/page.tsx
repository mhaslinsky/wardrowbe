'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { ApiError } from '@/lib/api';
import { useTrips } from '@/lib/hooks/use-trips';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

function errorKey(error: unknown): string {
  if (!(error instanceof ApiError)) return 'failed';
  if (error.status === 409) return 'overlap';
  if (error.status === 422) return 'validation';
  if (error.status === 429) return 'rateLimit';
  if (error.status === 503) {
    if (error.message === 'Trip weather unavailable') return 'weatherUnavailable';
    if (error.message === 'AI planning is disabled') return 'aiDisabled';
    return 'unavailable';
  }
  return 'failed';
}

export default function TripsPage() {
  const t = useTranslations('trips');
  const common = useTranslations('common');
  const constants = useTranslations('constants');
  const { trips, plan, remove } = useTrips();
  const [destination, setDestination] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [occasions, setOccasions] = useState(['casual']);
  const [validationError, setValidationError] = useState(false);
  const busy = plan.isPending || remove.isPending;
  const requestError = plan.error || remove.error;
  function resetErrors() {
    plan.reset();
    remove.reset();
    setValidationError(false);
  }
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4">
      <h1 className="text-2xl font-bold">{t('title')}</h1>
      <form className="grid gap-4 rounded-lg border p-4" onSubmit={(event) => {
        event.preventDefault();
        if (busy) return;
        resetErrors();
        const duration = (Date.parse(end) - Date.parse(start)) / 86400000;
        if (!destination.trim() || !Number.isInteger(duration) || duration < 0 || duration >= 14 || !occasions.length) {
          setValidationError(true);
          return;
        }
        plan.mutate({ destination: destination.trim(), start_date: start, end_date: end, occasions }, {
          onSuccess: () => {
            setDestination('');
            setStart('');
            setEnd('');
            setOccasions(['casual']);
          },
        });
      }}>
        <fieldset disabled={busy} className="grid gap-4" onChange={resetErrors}>
          <Label htmlFor="destination">{t('destination')}</Label>
          <Input id="destination" required maxLength={200} value={destination} onChange={(event) => setDestination(event.target.value)} />
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label htmlFor="departure">{t('departure')}</Label>
              <Input id="departure" type="date" required value={start} onChange={(event) => setStart(event.target.value)} />
            </div>
            <div>
              <Label htmlFor="return">{t('return')}</Label>
              <Input id="return" type="date" min={start} required value={end} onChange={(event) => setEnd(event.target.value)} />
            </div>
          </div>
          <Label htmlFor="occasion">{t('occasion')}</Label>
          <select id="occasion" className="rounded-md border bg-background p-2" multiple required value={occasions}
            onChange={(event) => setOccasions(Array.from(event.target.selectedOptions, (option) => option.value))}>
            {['casual', 'work', 'formal', 'sport', 'date'].map((value) =>
              <option key={value} value={value}>{constants(`occasions.${value}`)}</option>)}
          </select>
          <p className="text-sm text-muted-foreground">{t('limit')}</p>
          <Button disabled={busy}>{t(plan.isPending ? 'planning' : 'plan')}</Button>
        </fieldset>
      </form>
      {(validationError || requestError) && <p role="alert">{t(validationError ? 'validation' : remove.error && !plan.error ? 'deleteFailed' : errorKey(requestError))}</p>}
      {trips.isLoading && <p role="status">{t('loading')}</p>}
      {trips.isError && <p role="alert">{t('loadFailed')}</p>}
      {trips.data?.length === 0 && <p>{t('empty')}</p>}
      {trips.data?.map((trip) => (
        <section key={trip.id} className="space-y-3 rounded-lg border p-4">
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-xl font-semibold">{trip.destination}</h2>
            <Button variant="outline" disabled={busy} onClick={() => {
              if (busy || !confirm(t('deleteConfirm'))) return;
              resetErrors();
              remove.mutate(trip.id);
            }}>{common('delete')}</Button>
          </div>
          <ul className="space-y-2">{trip.days.map((day) => <li key={day.date}>
            <Link className="underline" href={`/dashboard/outfits/${day.outfit_id}`}>{day.date}</Link>
            {' · '}{t(day.weather_source)}{' · '}{t('temperature', { low: Math.round(day.low), high: Math.round(day.high) })}
          </li>)}</ul>
          <h3 className="font-semibold">{t('packing')}</h3>
          <ul>{trip.packing.map((item) => <li key={item.item_id}>
            <Link className="underline" href={`/dashboard/wardrobe?item=${item.item_id}`}>{item.name || t('unnamed')}</Link>
            {' · '}{t('quantity', { count: item.count, days: item.days_worn })}
          </li>)}</ul>
        </section>
      ))}
    </div>
  );
}
