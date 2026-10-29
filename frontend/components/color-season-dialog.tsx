'use client';

import { useEffect, useState } from 'react';
import { useTranslations } from 'next-intl';
import { api } from '@/lib/api';
import { CLOTHING_COLORS, Preferences } from '@/lib/types';
import { useClothingColors } from '@/lib/hooks/use-translated-constants';
import { Button } from '@/components/ui/button';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';

type ColorSeason = NonNullable<Preferences['color_season']>;
type Palette = { best: string[]; avoid: string[] };

export function ColorSeasonDialog({ season, replacesColors, onApply, onClose }: {
  season: ColorSeason;
  replacesColors: boolean;
  onApply: (palette: Palette) => void;
  onClose: () => void;
}) {
  const translate = useTranslations('settings');
  const common = useTranslations('common');
  const clothingColors = useClothingColors();
  const [palette, setPalette] = useState<Palette | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    setPalette(null);
    setFailed(false);
    api.get<Record<ColorSeason, Palette>>('/users/me/preferences/color-seasons')
      .then((palettes) => {
        const selected = palettes?.[season];
        if (!selected || ![selected.best, selected.avoid].every((colors) =>
          Array.isArray(colors) && colors.length > 0 && colors.every((color) =>
            CLOTHING_COLORS.some((known) => known.value === color)
          )
        ) || selected.best.some((color) => selected.avoid.includes(color))) {
          throw new Error('Invalid color season palette');
        }
        if (active) setPalette(selected);
      })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [season]);

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{translate(`colorSeason.${season}`)}</DialogTitle>
          <DialogDescription>{translate('colorSeason.prompt')}</DialogDescription>
        </DialogHeader>
        {replacesColors && <p className="text-sm text-muted-foreground">{translate('colorSeason.replaceColors')}</p>}
        {failed ? <p role="alert">{translate('colorSeason.loadFailed')}</p> : !palette ? (
          <p role="status">{common('loading')}</p>
        ) : (
          <>
            {(['best', 'avoid'] as const).map((group) => (
              <div key={group} role="group" aria-label={translate(group === 'best' ? 'colors.favoriteColors' : 'colors.colorsToAvoid')} className="space-y-2">
                <p className="text-sm font-medium">{translate(group === 'best' ? 'colors.favoriteColors' : 'colors.colorsToAvoid')}</p>
                <div className="flex flex-wrap gap-2">
                  {palette[group].map((color) => {
                    const swatch = clothingColors.find((entry) => entry.value === color)!;
                    return <span key={color} role="img" aria-label={swatch.name} title={swatch.name} className="h-8 w-8 rounded-full border-2 border-muted-foreground/20" style={{ backgroundColor: swatch.hex }} />;
                  })}
                </div>
              </div>
            ))}
          </>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{translate('colorSeason.notNow')}</Button>
          <Button disabled={!palette} onClick={() => { if (palette) onApply(palette); }}>{translate('colorSeason.useColors')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
