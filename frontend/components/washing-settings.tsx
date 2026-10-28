'use client';

import { useTranslations } from 'next-intl';
import { useItemTypes } from '@/lib/hooks/use-items';
import { useTypeLabel } from '@/lib/hooks/use-translated-constants';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';

interface WashingSettingsProps {
  values: Record<string, number>;
  defaults: Record<string, number>;
  onChange: (values: Record<string, number>) => void;
}

export function WashingSettings({ values, defaults, onChange }: WashingSettingsProps) {
  const translate = useTranslations('settings');
  const typeLabel = useTypeLabel();
  const { data: types, isLoading, isError } = useItemTypes();
  const ownedTypes = types?.filter(({ type }) => type in defaults) ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>{translate('washing.title')}</CardTitle>
        <CardDescription>{translate('washing.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? <p>{translate('washing.loading')}</p> : isError ? (
          <p role="alert">{translate('washing.error')}</p>
        ) : ownedTypes.length === 0 ? <p>{translate('washing.empty')}</p> : (
          ownedTypes.map(({ type }) => (
            <div key={type} className="flex flex-wrap items-center gap-3">
              <Label htmlFor={`wash-${type}`} className="min-w-24">{typeLabel(type)}</Label>
              <select
                id={`wash-${type}`}
                className="h-10 rounded-md border bg-background px-3 text-sm"
                value={values[type] ?? defaults[type]}
                onChange={(event) => onChange({ ...values, [type]: Number(event.target.value) })}
              >
                {Array.from({ length: 30 }, (_, index) => index + 1).map((count) => (
                  <option key={count} value={count}>{count}</option>
                ))}
                <option value={999}>{translate('washing.never')}</option>
              </select>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={values[type] === undefined}
                onClick={() => {
                  const next = { ...values };
                  delete next[type];
                  onChange(next);
                }}
              >
                {translate('washing.reset')}
              </Button>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
