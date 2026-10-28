'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { useTranslations } from 'next-intl';
import { useConsideringBuyCheck, useBuyCheckItems, type BuyCheckReason } from '@/lib/hooks/use-considering-buy-check';
import { Button } from '@/components/ui/button';

export function ConsideringBuyCheck({ itemId, disabled }: { itemId: string; disabled?: boolean }) {
  const t = useTranslations('wardrobe.considering.buyCheck');
  const check = useConsideringBuyCheck(itemId);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const result = check.data;
  const itemNames = useBuyCheckItems(result);
  const error = check.isError ? 'unavailable' : result?.status === 'error'
    ? result.error === 'missing_key' || result.error === 'disabled' ? 'notConfigured' : 'unavailable'
    : null;
  function reasons(rows: BuyCheckReason[], group: 'pluses' | 'minuses' | 'unknowns') {
    if (!rows.length) return null;
    return <div><h4 className="font-medium">{t(group)}</h4><ul className="space-y-2">
      {rows.map((reason, reasonIndex) => <li key={`${reason.kind}:${reasonIndex}`} className="text-sm">
        <span>{t(reason.kind === 'skip' ? 'skipReason' : reason.kind === 'wrong_size' ? 'wrongSizeReason' : reason.kind === 'duplicate' ? 'duplicateReason' : reason.kind)}</span>
        {reason.citations.map((citation, citationIndex) => <span key={`${citation.source_id}:${citationIndex}`} className="block text-muted-foreground">
          {citation.item_id ? <Link className="underline" href={`/dashboard/wardrobe?item=${encodeURIComponent(citation.item_id)}`}>{itemNames.get(citation.item_id) || t('item')}</Link>
            : <Link className="underline" href="/dashboard/fit-profile">{t('profile')}</Link>}
          {citation.quote && <>: “{citation.quote}”</>}
          {citation.value != null && <>: {citation.value}</>}
        </span>)}
      </li>)}
    </ul></div>;
  }
  return <section className="min-w-0 space-y-3 break-words rounded-md border p-3" aria-label={t('title')}>
    <div className="flex flex-wrap items-center gap-2">
      <h3 className="text-sm font-semibold">{t('title')}</h3>
      <Button variant="outline" disabled={disabled || check.isPending} onClick={() => {
        if (check.isPending) return;
        check.reset();
        request.current = new AbortController();
        check.mutate(request.current.signal);
      }}>{t('check')}</Button>
    </div>
    {check.isPending && <p role="status" className="text-sm">{t('loading')}</p>}
    {error && <p role="alert" className="text-sm">{t(error)}</p>}
    {result && !error && <div role="status" className="space-y-3">
      {result.near_duplicate && <p className="text-sm font-medium">{t('nearDuplicate')}</p>}
      <p className="text-sm font-medium">{result.status === 'scored' ? t('scored', { percentage: result.percentage! }) : t(result.status)}</p>
      {reasons(result.pluses, 'pluses')}
      {reasons(result.minuses, 'minuses')}
      {reasons(result.unknowns, 'unknowns')}
      <div className="space-y-2 text-sm"><h4 className="font-medium">{t('outfits')}</h4>
        {result.outfits.status === 'known' ? <>
          <p>{t('outfitCount', { count: result.outfits.count! })}</p>
          <ul className="space-y-2">{result.outfits.ideas?.map((idea, ideaIndex) => <li key={ideaIndex} className="flex flex-wrap gap-x-2 gap-y-1">
            {idea.item_ids.map((id, itemIndex) => <Link key={id} className="underline" href={`/dashboard/wardrobe?item=${encodeURIComponent(id)}`}>{itemNames.get(id) || t(id === itemId ? 'thisItem' : 'outfitItem', { number: itemIndex + 1 })}</Link>)}
          </li>)}</ul>
          {(result.outfits.pool_truncated || result.outfits.idea_limit_truncated) && <p className="text-muted-foreground">{t('sampled')}</p>}
        </> : <p>{t(result.outfits.status === 'not_applicable' ? 'outfitsNotApplicable' : 'outfitsUnknown')}</p>}
      </div>
    </div>}
  </section>;
}
