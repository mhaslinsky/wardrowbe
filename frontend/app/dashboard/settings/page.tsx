'use client';

import { AccessTokenSettings } from '@/components/access-token-settings';
import { WashingSettings } from '@/components/washing-settings';
import { ColorSeasonDialog } from '@/components/color-season-dialog';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PreviewPhotoSettings } from '@/components/preview-photo-settings';
import { useState, useEffect, useMemo, useRef } from 'react';
import { useAutosaveFields } from '@/lib/hooks/use-autosave-fields';
import { useSession } from 'next-auth/react';
import { Loader2, RotateCcw, Check, Plus, Trash2, ChevronUp, ChevronDown, Server, MapPin, Navigation, Ruler } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Slider } from '@/components/ui/slider';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { usePreferences, useUpdatePreferences, useResetPreferences, useTestAIEndpoint } from '@/lib/hooks/use-preferences';
import { useUserProfile, useUpdateUserProfile, UserProfile, UserProfileUpdate } from '@/lib/hooks/use-user';
import {
  getNetworkLocationUrl,
  formatReverseGeocodedLocation,
  isNetworkLocationFallbackEnabled,
  LOCATION_NAME_MAX_LENGTH,
  resolveNetworkLocation,
} from '@/lib/location';
import { Preferences, StyleProfile, AIEndpoint } from '@/lib/types';
import { useClothingColors, useOccasionOptions } from '@/lib/hooks/use-translated-constants';
import { toF, toCelsius } from '@/lib/temperature';
import { PREFERENCE_DEFAULTS } from '@/lib/generated/preference-defaults';
import { AVOID_REPEAT_DAYS, STYLE_SCORE, TEMPERATURE_THRESHOLDS_CELSIUS } from '@/lib/generated/scales';
import { DEFAULT_STYLE_PROFILE } from '@/lib/preferences';
import { toast } from 'sonner';
import { useTranslations } from 'next-intl';

// Kept out of the catalogs because model names are example input, identical in every locale.
const EXAMPLE_VISION_MODEL = 'moondream';
const EXAMPLE_TEXT_MODEL = 'phi3:mini';

const CM_TO_IN = 0.393701;
const IN_TO_CM = 2.54;
const KG_TO_LBS = 2.20462;
const LBS_TO_KG = 0.453592;

function convertMeasurement(value: number, key: string, from: string, to: string): number {
  if (from === to) return value;
  const isWeight = key === 'weight';
  if (from === 'metric' && to === 'imperial') {
    return Math.round((isWeight ? value * KG_TO_LBS : value * CM_TO_IN) * 10) / 10;
  }
  return Math.round((isWeight ? value * LBS_TO_KG : value * IN_TO_CM) * 10) / 10;
}

const BODY_MEASUREMENT_FIELDS = [
  { key: 'height', unitMetric: 'cm', unitImperial: 'in', exampleMetric: '178', exampleImperial: '70' },
  { key: 'weight', unitMetric: 'kg', unitImperial: 'lbs', exampleMetric: '75', exampleImperial: '165' },
  { key: 'chest', unitMetric: 'cm', unitImperial: 'in', exampleMetric: '96', exampleImperial: '38' },
  { key: 'waist', unitMetric: 'cm', unitImperial: 'in', exampleMetric: '82', exampleImperial: '32' },
  { key: 'hips', unitMetric: 'cm', unitImperial: 'in', exampleMetric: '98', exampleImperial: '39' },
  { key: 'inseam', unitMetric: 'cm', unitImperial: 'in', exampleMetric: '81', exampleImperial: '32' },
] as const;

const NUMERIC_MEASUREMENT_KEYS: readonly string[] = BODY_MEASUREMENT_FIELDS.map((f) => f.key);

interface EndpointTestResult {
  status: 'connected' | 'error' | 'testing' | null;
  models?: string[];
  visionModels?: string[];
  textModels?: string[];
  error?: string;
}

function ColorPicker({
  selected,
  onChange,
  label,
}: {
  selected: string[];
  onChange: (colors: string[]) => void;
  label: string;
}) {
  const clothingColors = useClothingColors();

  const toggleColor = (color: string) => {
    if (selected.includes(color)) {
      onChange(selected.filter((c) => c !== color));
    } else {
      onChange([...selected, color]);
    }
  };

  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <div className="flex flex-wrap gap-2">
        {clothingColors.map((color) => {
          const isSelected = selected.includes(color.value);
          return (
            <button
              key={color.value}
              type="button"
              onClick={() => toggleColor(color.value)}
              className={`w-8 h-8 rounded-full border-2 transition-all ${
                isSelected
                  ? 'border-primary ring-2 ring-primary/30 scale-110'
                  : 'border-muted-foreground/20 hover:border-muted-foreground/40'
              }`}
              style={{ backgroundColor: color.hex }}
              title={color.name}
            >
              {isSelected && (
                <Check
                  className={`h-4 w-4 mx-auto ${
                    color.value === 'white' || color.value === 'yellow' || color.value === 'beige'
                      ? 'text-black'
                      : 'text-white'
                  }`}
                />
              )}
            </button>
          );
        })}
      </div>
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {selected.map((color) => {
            const colorInfo = clothingColors.find((c) => c.value === color);
            return (
              <Badge key={color} variant="secondary" className="gap-1">
                <div
                  className="w-2 h-2 rounded-full"
                  style={{ backgroundColor: colorInfo?.hex }}
                />
                {colorInfo?.name}
              </Badge>
            );
          })}
        </div>
      )}
    </div>
  );
}

function StyleSlider({
  label,
  value,
  onChange,
  onCommit,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  onCommit: (value: number) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex justify-between items-center">
        <Label>{label}</Label>
        <span className="text-sm text-muted-foreground">{value}%</span>
      </div>
      <Slider
        value={[value]}
        onValueChange={(values) => onChange(values[0])}
        onValueCommit={(values) => onCommit(values[0])}
        min={STYLE_SCORE.min}
        max={STYLE_SCORE.max}
        step={10}
      />
    </div>
  );
}

type UnitSystem = 'metric' | 'imperial';
const measurementUnitsByUser = new Map<string, UnitSystem>();
type ProfileFields = {
  location_name: string;
  location_lat: string;
  location_lon: string;
  timezone: string;
  body_measurements: Record<string, string>;
};

function displayProfile(profile: Partial<UserProfile>, unitSystem: UnitSystem): ProfileFields {
  return {
    location_name: profile.location_name || '',
    location_lat: profile.location_lat?.toString() || '',
    location_lon: profile.location_lon?.toString() || '',
    timezone: profile.timezone || 'UTC',
    body_measurements: Object.fromEntries(Object.entries(profile.body_measurements || {}).map(([key, value]) => [
      key, String(NUMERIC_MEASUREMENT_KEYS.includes(key) && typeof value === 'number'
        ? convertMeasurement(value, key, 'metric', unitSystem) : value),
    ])),
  };
}

export default function SettingsPage() {
  const router = useRouter();
  const navigation = useTranslations('nav');
  useEffect(() => {
    const redirectFitProfile = () => {
      if (window.location.hash === '#fit-profile') router.replace('/dashboard/fit-profile');
    };
    redirectFitProfile();
    window.addEventListener('hashchange', redirectFitProfile);
    return () => window.removeEventListener('hashchange', redirectFitProfile);
  }, [router]);
  const t = useTranslations('settings');
  const tc = useTranslations('common');
  const tConst = useTranslations('constants');
  const { data: session } = useSession();
  const { data: preferences, isLoading } = usePreferences();
  const { data: userProfile, isLoading: isLoadingProfile } = useUserProfile();
  const updatePreferences = useUpdatePreferences({ toastsOwnErrors: true });
  const resetPreferences = useResetPreferences({ toastsOwnErrors: true });
  const testEndpoint = useTestAIEndpoint();
  const updateUserProfile = useUpdateUserProfile({ toastsOwnErrors: true });

  const owner = session?.user.id || userProfile?.id;
  const reportSaveError = (error: unknown) => toast.error(error instanceof Error ? error.message : t('autosave.error'));
  const autosave = useAutosaveFields<Preferences>(preferences, updatePreferences.mutateAsync, reportSaveError, owner ? { resource: 'preferences', user: owner } : undefined);
  const formData = autosave.values;
  const activeStart = formData.active_start_hour ?? preferences?.default_active_start_hour ?? 0;
  const activeEnd = formData.active_end_hour ?? preferences?.default_active_end_hour ?? 21;
  const activeHoursInvalid = activeEnd - activeStart < 2;
  const occasions = useOccasionOptions([formData.default_occasion, preferences?.default_occasion]);
  const [standingRulesError, setStandingRulesError] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [paletteSeason, setPaletteSeason] = useState<NonNullable<Preferences['color_season']> | null>(null);
  const [endpointTests, setEndpointTests] = useState<Record<number, EndpointTestResult>>({});

  const [isGettingLocation, setIsGettingLocation] = useState(false);
  const [profileErrors, setProfileErrors] = useState<Partial<Record<keyof ProfileFields, string>>>({});
  const clearProfileError = (key: keyof ProfileFields) => setProfileErrors((previous) => { const next = { ...previous }; delete next[key]; return next; });
  const [unitSystem, setUnitSystem] = useState<UnitSystem>(() => {
    if (owner && measurementUnitsByUser.has(owner)) return measurementUnitsByUser.get(owner)!;
    if (typeof window !== 'undefined') {
      return (localStorage.getItem('wardrowbe_unit_system') as UnitSystem) || 'metric';
    }
    return 'metric';
  });
  const unitSystemRef = useRef(unitSystem);
  if (owner && !measurementUnitsByUser.has(owner)) measurementUnitsByUser.set(owner, unitSystem);
  unitSystemRef.current = (owner && measurementUnitsByUser.get(owner)) || unitSystem;
  useEffect(() => {
    const synchronize = (event?: Event) => {
      const change = event as CustomEvent<{ owner?: string; units: UnitSystem }> | undefined;
      if (change && change.detail.owner !== owner) return;
      const selected = change?.detail.units || (owner && measurementUnitsByUser.get(owner));
      if (selected) { unitSystemRef.current = selected; setUnitSystem(selected); }
    };
    synchronize();
    window.addEventListener('wardrowbe-units', synchronize);
    return () => window.removeEventListener('wardrowbe-units', synchronize);
  }, [owner]);
  const initialProfile = useMemo(() => userProfile ? displayProfile(userProfile, unitSystemRef.current) : undefined, [userProfile]);
  const profileAutosave = useAutosaveFields<ProfileFields>(initialProfile, async (patch) => {
    const payload: UserProfileUpdate = {};
    if ('location_name' in patch) payload.location_name = patch.location_name;
    if ('timezone' in patch) payload.timezone = patch.timezone;
    if ('location_lat' in patch) payload.location_lat = Number(patch.location_lat);
    if ('location_lon' in patch) payload.location_lon = Number(patch.location_lon);
    if ('body_measurements' in patch) {
      const parsed = Object.fromEntries(Object.entries(patch.body_measurements || {})
        .filter(([, value]) => value.trim())
        .map(([key, value]) => [key, NUMERIC_MEASUREMENT_KEYS.includes(key)
          ? convertMeasurement(Number(value), key, unitSystemRef.current, 'metric') : value.trim()]));
      payload.body_measurements = Object.keys(parsed).length ? parsed : null;
    }
    return displayProfile(await updateUserProfile.mutateAsync(payload), unitSystemRef.current);
  }, reportSaveError, owner ? { resource: 'profile', user: owner } : undefined);
  const locationName = profileAutosave.values.location_name || '';
  const locationLat = profileAutosave.values.location_lat || '';
  const locationLon = profileAutosave.values.location_lon || '';
  const timezone = profileAutosave.values.timezone || 'UTC';
  const measurements = profileAutosave.values.body_measurements || {};
  const setLocationName = (value: string) => { clearProfileError('location_name'); profileAutosave.stage({ location_name: value }); };
  const stageCoordinate = (key: 'location_lat' | 'location_lon', value: string) => {
    clearProfileError(key); profileAutosave.stage({ [key]: value });
    const current = profileAutosave.current.current;
    if (!(['location_lat', 'location_lon'] as const).every((field) => {
      const raw = current[field] || '';
      return raw.trim() && Number.isFinite(Number(raw)) && Math.abs(Number(raw)) <= (field === 'location_lat' ? 90 : 180);
    })) profileAutosave.cancelPending('location_lat', 'location_lon');
  };
  const setLocationLat = (value: string) => stageCoordinate('location_lat', value);
  const setLocationLon = (value: string) => stageCoordinate('location_lon', value);
  const setTimezone = (value: string) => { void profileAutosave.commit({ timezone: value }); };

  const saveProfileField = (key: 'location_name' | 'location_lat' | 'location_lon') => {
    const current = profileAutosave.current.current;
    if (key === 'location_name') {
      if (profileAutosave.isStaged(key)) void profileAutosave.commit({ location_name: current.location_name || '' });
      return;
    }
    if (!profileAutosave.isStaged('location_lat') && !profileAutosave.isStaged('location_lon')) return;
    const errors: Partial<Record<keyof ProfileFields, string>> = {};
    for (const coordinate of ['location_lat', 'location_lon'] as const) {
      const raw = current[coordinate] || '';
      const value = Number(raw);
      const limit = coordinate === 'location_lat' ? 90 : 180;
      if (!raw.trim() || !Number.isFinite(value)) errors[coordinate] = t('location.errors.invalidCoordinates');
      else if (value < -limit || value > limit) errors[coordinate] = t(coordinate === 'location_lat' ? 'location.errors.latitudeRange' : 'location.errors.longitudeRange');
    }
    setProfileErrors((previous) => {
      const next = { ...previous }; delete next.location_lat; delete next.location_lon;
      return { ...next, ...errors };
    });
    if (Object.keys(errors).length) { profileAutosave.cancelPending('location_lat', 'location_lon'); return; }
    void profileAutosave.commit({ location_lat: current.location_lat!, location_lon: current.location_lon! }, { atomic: true });
  };

  const detectLocationFromNetwork = async () => {
    const response = await fetch(getNetworkLocationUrl(), {
      headers: { Accept: 'application/json' },
    });

    if (!response.ok) {
      throw new Error(
        t('location.errors.networkLookupFailed', {
          status: `${response.status}${response.statusText ? ` ${response.statusText}` : ''}`,
        })
      );
    }

    const data = await response.json();
    const resolved = resolveNetworkLocation(data, timezone);
    clearProfileError('location_lat');
    clearProfileError('location_lon');
    if (resolved.locationName) clearProfileError('location_name');
    void profileAutosave.commit({
      location_lat: resolved.lat,
      location_lon: resolved.lon,
      ...(resolved.locationName ? { location_name: resolved.locationName } : {}),
      ...(resolved.timezone ? { timezone: resolved.timezone } : {}),
    }, { atomic: true });
    return resolved;
  };

  const describeGeolocationFailure = (error: GeolocationPositionError): string => {
    switch (error.code) {
      case error.PERMISSION_DENIED:
        return t('location.errors.geolocationDenied');
      case error.POSITION_UNAVAILABLE:
        return t('location.errors.geolocationUnavailable');
      case error.TIMEOUT:
        return t('location.errors.geolocationTimeout');
      default:
        return error.message
          ? t('location.errors.geolocationFailed', { message: error.message })
          : t('location.errors.geolocationFailedGeneric');
    }
  };

  const handleGetCurrentLocation = () => {
    setIsGettingLocation(true);
    const finalizeFromCoordinates = async (lat: string, lon: string) => {
      // Reverse geocode to get city name
      try {
        const response = await fetch(
          `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`,
          { headers: { 'User-Agent': 'WardrobeAI/1.0' } }
        );
        if (response.ok) {
          const data = await response.json();
          const nextLocationName = formatReverseGeocodedLocation(data);
          if (nextLocationName) {
            return nextLocationName;
          }
        }
      } catch {
        // Ignore geocoding errors, we still have coordinates
      }

      return undefined;
    };

    const fallbackToNetworkLocation = async (reason?: string) => {
      if (!isNetworkLocationFallbackEnabled()) {
        toast.error(reason || t('location.errors.unableToDetect'));
        setIsGettingLocation(false);
        return;
      }
      try {
        await detectLocationFromNetwork();
        toast.success(
          reason
            ? t('location.approximateDetectedWithReason', { reason })
            : t('location.approximateDetected')
        );
      } catch (fallbackError) {
        const fallbackMessage = fallbackError instanceof Error
          ? fallbackError.message
          : t('location.errors.unableToDetect');
        toast.error(fallbackMessage);
      } finally {
        setIsGettingLocation(false);
      }
    };

    if (!navigator.geolocation) {
      void fallbackToNetworkLocation(t('location.errors.geolocationUnsupported'));
      return;
    }

    navigator.geolocation.getCurrentPosition(
      async (position) => {
        const lat = position.coords.latitude.toFixed(6);
        const lon = position.coords.longitude.toFixed(6);
        const originalName = profileAutosave.current.current.location_name;
        clearProfileError('location_lat');
        clearProfileError('location_lon');
        const coordinatesSaved = profileAutosave.commit({ location_lat: lat, location_lon: lon }, { atomic: true });
        const name = await finalizeFromCoordinates(lat, lon);
        await coordinatesSaved;
        const confirmed = profileAutosave.confirmed();
        const coordinatesMatch = Number(confirmed.location_lat) === Number(lat) && Number(confirmed.location_lon) === Number(lon);
        const current = profileAutosave.current.current;
        if (coordinatesMatch && name && Number(current.location_lat) === Number(lat) && Number(current.location_lon) === Number(lon) && current.location_name === originalName) {
          void profileAutosave.commit({ location_name: name });
        }
        setIsGettingLocation(false);
        if (coordinatesMatch) toast.success(t('location.detected'));
      },
      (error) => {
        void fallbackToNetworkLocation(describeGeolocationFailure(error));
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const handleToggleUnits = () => {
    let conversion: { from: UnitSystem; to: UnitSystem };
    void profileAutosave.transform('body_measurements', (values) => {
      conversion ??= { from: unitSystemRef.current, to: unitSystemRef.current === 'metric' ? 'imperial' : 'metric' };
      return Object.fromEntries(Object.entries(values || {}).map(([key, value]) => [key,
        NUMERIC_MEASUREMENT_KEYS.includes(key) && value.trim() && Number.isFinite(Number(value))
          ? String(convertMeasurement(Number(value), key, conversion.from, conversion.to)) : value,
      ]));
    }, () => {
      unitSystemRef.current = conversion.to;
      if (owner) measurementUnitsByUser.set(owner, conversion.to);
      setUnitSystem(conversion.to);
      localStorage.setItem('wardrowbe_unit_system', conversion.to);
      window.dispatchEvent(new CustomEvent('wardrowbe-units', { detail: { owner, units: conversion.to } }));
    }).catch(reportSaveError);
  };

  const handleMeasurementChange = (key: string, value: string) => {
    clearProfileError('body_measurements');
    profileAutosave.stage({ body_measurements: { ...profileAutosave.current.current.body_measurements, [key]: value } });
    if (Object.entries(profileAutosave.current.current.body_measurements || {}).some(([field, raw]) =>
      NUMERIC_MEASUREMENT_KEYS.includes(field) && raw.trim() && (!Number.isFinite(Number(raw)) || Number(raw) <= 0),
    )) profileAutosave.cancelPending('body_measurements');
  };

  const saveMeasurements = () => {
    if (!profileAutosave.isStaged('body_measurements')) return;
    const current = profileAutosave.current.current.body_measurements || {};
    for (const [key, value] of Object.entries(current)) {
      if (value.trim() && NUMERIC_MEASUREMENT_KEYS.includes(key) && (!Number.isFinite(Number(value)) || Number(value) <= 0)) {
        profileAutosave.cancelPending('body_measurements');
        setProfileErrors((previous) => ({ ...previous, body_measurements: t('body.errors.positiveNumber', { field: t(`body.fields.${key}`) }) }));
        return;
      }
    }
    clearProfileError('body_measurements');
    void profileAutosave.commit({ body_measurements: current });
  };

  const handleTestEndpoint = async (index: number, url: string) => {
    setEndpointTests((prev) => ({ ...prev, [index]: { status: 'testing' } }));
    try {
      const result = await testEndpoint.mutateAsync(url);
      setEndpointTests((prev) => ({
        ...prev,
        [index]: {
          status: result.status,
          models: result.available_models,
          visionModels: result.vision_models,
          textModels: result.text_models,
          error: result.error,
        },
      }));
    } catch (error) {
      setEndpointTests((prev) => ({
        ...prev,
        [index]: { status: 'error', error: t('aiEndpoints.testFailed') },
      }));
    }
  };

  const updateField = <Key extends keyof Preferences>(key: Key, value: Preferences[Key], save = true) => {
    const patch: Partial<Preferences> = { [key]: value };
    const current = autosave.current.current;
    if (key === 'color_favorites' && Array.isArray(value) && current.color_avoid?.some((color) => (value as string[]).includes(color))) {
      patch.color_avoid = (current.color_avoid || []).filter((color) => !(value as string[]).includes(color));
    } else if (key === 'color_avoid' && Array.isArray(value) && current.color_favorites?.some((color) => (value as string[]).includes(color))) {
      patch.color_favorites = (current.color_favorites || []).filter((color) => !(value as string[]).includes(color));
    }
    if (save) void autosave.commit(patch);
    else autosave.stage(patch);
  };

  const updateStyleProfile = (key: keyof StyleProfile, value: number, save = false) => {
    updateField('style_profile', {
      ...(autosave.current.current.style_profile || DEFAULT_STYLE_PROFILE),
      [key]: value,
    }, save);
  };

  const updateActiveHours = (key: 'active_start_hour' | 'active_end_hour', value: number) => {
    const current = autosave.current.current;
    const patch = {
      active_start_hour: current.active_start_hour ?? preferences?.default_active_start_hour ?? 0,
      active_end_hour: current.active_end_hour ?? preferences?.default_active_end_hour ?? 21,
      [key]: value,
    };
    autosave.stage(patch);
    if (patch.active_end_hour - patch.active_start_hour < 2) {
      autosave.cancelPending('active_start_hour', 'active_end_hour');
    } else {
      void autosave.commit(patch, { atomic: true });
    }
  };

  const saveStandingRules = (text: string) => {
    if (!autosave.isStaged('standing_rules')) return;
    const rules = text.split('\n').map((rule) => rule.trim()).filter(Boolean);
    if (rules.length > 20 || rules.some((rule) => rule.length > 300)) {
      autosave.cancelPending('standing_rules');
      setStandingRulesError(true);
      return;
    }
    setStandingRulesError(false);
    updateField('standing_rules', rules);
  };

  const saveStagedField = <Key extends keyof Preferences>(key: Key) => {
    const value = autosave.current.current[key];
    if (autosave.isStaged(key) && value !== undefined) updateField(key, value);
  };

  const flushDrafts = useRef(() => {});
  flushDrafts.current = () => {
    for (const key of autosave.draftKeys()) {
      if (key === 'active_start_hour' || key === 'active_end_hour') continue;
      if (key === 'standing_rules') saveStandingRules((autosave.current.current.standing_rules || []).join('\n'));
      else saveStagedField(key);
    }
    for (const key of profileAutosave.draftKeys()) {
      if (key === 'body_measurements') saveMeasurements();
      else if (key !== 'timezone') saveProfileField(key);
    }
  };
  useEffect(() => () => flushDrafts.current(), []);

  const handleReset = async () => {
    if (!confirm(t('confirmReset'))) return;
    setResetting(true);
    try {
      await Promise.all([profileAutosave.wait(), autosave.reset(() => resetPreferences.mutateAsync(autosave.confirmed().preview_preferences!.rev))]);
      setStandingRulesError(false);
      setPaletteSeason(null);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('autosave.error'));
    } finally {
      setResetting(false);
    }
  };

  useEffect(() => {
    if (!autosave.busy && !profileAutosave.busy && !autosave.dirty && !profileAutosave.dirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', preventUnload);
    return () => window.removeEventListener('beforeunload', preventUnload);
  }, [autosave.busy, profileAutosave.busy, autosave.dirty, profileAutosave.dirty]);

  useEffect(() => {
    const warnBeforeLeaving = (event: MouseEvent) => {
      const rules = (autosave.current.current.standing_rules || []).map((rule) => rule.trim()).filter(Boolean);
      if (!autosave.isStaged('standing_rules') || (rules.length <= 20 && rules.every((rule) => rule.length <= 300))) return;
      const link = (event.target as Element | null)?.closest('a[href]') as HTMLAnchorElement | null;
      if (!link || link.target === '_blank' || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const destination = new URL(link.href, window.location.href);
      if (destination.origin !== window.location.origin || destination.pathname === window.location.pathname) return;
      if (!window.confirm(t('confirmLeave'))) { event.preventDefault(); event.stopImmediatePropagation(); }
    };
    document.addEventListener('click', warnBeforeLeaving, true);
    return () => document.removeEventListener('click', warnBeforeLeaving, true);
  }, [autosave, t]);

  const status = autosave.busy || profileAutosave.busy || resetting ? 'saving'
    : autosave.status === 'error' || profileAutosave.status === 'error' || standingRulesError || activeHoursInvalid || Object.keys(profileErrors).length ? 'error' : 'saved';

  if (isLoading || isLoadingProfile) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t('title')}</h1>
          <p className="text-sm text-muted-foreground">
            {t('subtitle')}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={handleReset} disabled={resetting}>
            <RotateCcw className="mr-2 h-4 w-4" />
            {t('reset')}
          </Button>
          {Object.entries(profileErrors).map(([key, message]) => <p key={key} role="alert" className="self-center text-sm text-destructive">{message}</p>)}
          <p role={status === 'error' ? 'alert' : 'status'} className={status === 'error' ? 'self-center text-sm text-destructive' : 'self-center text-sm text-muted-foreground'}>
            {t('autosave.' + status)}
          </p>
        </div>
      </div>

      <fieldset disabled={resetting} className="grid min-w-0 gap-6">
        <Link href="/dashboard/fit-profile" className="text-sm text-primary underline underline-offset-4">{navigation('fitProfile')}</Link>
        <AccessTokenSettings />
        {/* Account Section */}
        <Card>
          <CardHeader>
            <CardTitle>{t('account.title')}</CardTitle>
            <CardDescription>{t('account.description')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('account.name')}</Label>
                <Input value={userProfile?.display_name || ''} disabled />
              </div>
              <div className="space-y-2">
                <Label>{t('account.email')}</Label>
                <Input value={userProfile?.email || ''} disabled />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Location Section */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MapPin className="h-5 w-5" />
              {t('location.title')}
            </CardTitle>
            <CardDescription>
              {t('location.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>{t('location.cityLabel')}</Label>
              <Input
                value={locationName}
                maxLength={LOCATION_NAME_MAX_LENGTH}
                onChange={(event) => setLocationName(event.target.value)}
                onBlur={() => saveProfileField('location_name')}
                placeholder={t('location.cityPlaceholder')}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('location.latitude')}</Label>
                <Input
                  type="number"
                  step="0.000001"
                  value={locationLat}
                  onChange={(event) => setLocationLat(event.target.value)}
                  onBlur={() => saveProfileField('location_lat')}
                  placeholder={tc('example', { value: '51.5074' })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('location.longitude')}</Label>
                <Input
                  type="number"
                  step="0.000001"
                  value={locationLon}
                  onChange={(event) => setLocationLon(event.target.value)}
                  onBlur={() => saveProfileField('location_lon')}
                  placeholder={tc('example', { value: '-0.1278' })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t('location.timezone')}</Label>
              <Select value={timezone} onValueChange={setTimezone}>
                <SelectTrigger>
                  <SelectValue placeholder={t('location.selectTimezone')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="UTC">{tConst('timezones.UTC')}</SelectItem>
                  <SelectItem value="America/New_York">{tConst('timezones.America/New_York')}</SelectItem>
                  <SelectItem value="America/Chicago">{tConst('timezones.America/Chicago')}</SelectItem>
                  <SelectItem value="America/Denver">{tConst('timezones.America/Denver')}</SelectItem>
                  <SelectItem value="America/Los_Angeles">{tConst('timezones.America/Los_Angeles')}</SelectItem>
                  <SelectItem value="Europe/London">{tConst('timezones.Europe/London')}</SelectItem>
                  <SelectItem value="Europe/Paris">{tConst('timezones.Europe/Paris')}</SelectItem>
                  <SelectItem value="Europe/Berlin">{tConst('timezones.Europe/Berlin')}</SelectItem>
                  <SelectItem value="Asia/Tokyo">{tConst('timezones.Asia/Tokyo')}</SelectItem>
                  <SelectItem value="Asia/Shanghai">{tConst('timezones.Asia/Shanghai')}</SelectItem>
                  <SelectItem value="Asia/Kolkata">{tConst('timezones.Asia/Kolkata')}</SelectItem>
                  <SelectItem value="Asia/Kathmandu">{tConst('timezones.Asia/Kathmandu')}</SelectItem>
                  <SelectItem value="Asia/Dubai">{tConst('timezones.Asia/Dubai')}</SelectItem>
                  <SelectItem value="Australia/Sydney">{tConst('timezones.Australia/Sydney')}</SelectItem>
                  <SelectItem value="Pacific/Auckland">{tConst('timezones.Pacific/Auckland')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={handleGetCurrentLocation}
                disabled={isGettingLocation}
              >
                {isGettingLocation ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Navigation className="h-4 w-4 mr-2" />
                )}
                {t('location.useMyLocation')}
              </Button>

            </div>
            {!locationLat && !locationLon && (
              <p className="text-sm text-amber-600 dark:text-amber-400">
                {t('location.required')}
              </p>
            )}
          </CardContent>
        </Card>

        {/* Body Measurements */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Ruler className="h-5 w-5" />
              {t('body.title')}
            </CardTitle>
            <CardDescription>{t('body.description')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <div className="flex items-center justify-between">
              <Label>{t('body.unitSystem')}</Label>
              <Button variant="outline" size="sm" onClick={handleToggleUnits}>
                {unitSystem === 'metric' ? t('body.metric') : t('body.imperial')}
              </Button>
            </div>

            <div>
              <Label className="text-muted-foreground mb-3 block">{t('body.bodyLabel')}</Label>
              <div className="grid gap-3 sm:grid-cols-2">
                {BODY_MEASUREMENT_FIELDS.map((field) => {
                  const unit = unitSystem === 'metric' ? field.unitMetric : field.unitImperial;
                  const placeholder = tc('example', { value: unitSystem === 'metric' ? field.exampleMetric : field.exampleImperial });
                  return (
                    <div key={field.key} className="space-y-1">
                      <Label className="text-sm">{t(`body.fields.${field.key}`)}</Label>
                      <div className="flex items-center gap-2">
                        <Input
                          type="number"
                          step="0.1"
                          min="0"
                          value={measurements[field.key] ?? ''}
                          onChange={(event) => handleMeasurementChange(field.key, event.target.value)}
                          onBlur={saveMeasurements}
                          placeholder={placeholder}
                          className="flex-1"
                        />
                        <span className="text-sm text-muted-foreground min-w-[2rem] text-center">{unit}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            <div>
              <Label className="text-muted-foreground mb-3 block">{t('body.sizesLabel')}</Label>
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.entries({
                  shirt_size: t('body.sizeFields.shirtSize'),
                  pants_size: t('body.sizeFields.pantsSize'),
                  dress_size: t('body.sizeFields.dressSize'),
                  shoe_size: t('body.sizeFields.shoeSize'),
                }).map(([key, label]) => (
                  <div key={key} className="space-y-1">
                    <Label className="text-sm">{label}</Label>
                    <Input
                      value={measurements[key] ?? ''}
                      onChange={(event) => handleMeasurementChange(key, event.target.value)}
                      onBlur={saveMeasurements}
                      placeholder={t(`body.sizePlaceholders.${key}`)}
                    />
                  </div>
                ))}
              </div>
            </div>

          </CardContent>
        </Card>

        {/* Color Preferences */}
        <Card>
          <CardHeader>
            <CardTitle>{t('colors.favoriteColors')}</CardTitle>
            <CardDescription>
              {t('colors.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <ColorPicker
              label={t('colors.favoriteColors')}
              selected={formData.color_favorites || []}
              onChange={(colors) => updateField('color_favorites', colors)}
            />
            <ColorPicker
              label={t('colors.colorsToAvoid')}
              selected={formData.color_avoid || []}
              onChange={(colors) => updateField('color_avoid', colors)}
            />
            <div className="space-y-2">
              <Label htmlFor="color-season">{t('colorSeason.label')}</Label>
              <select
                id="color-season"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={formData.color_season || ''}
                onChange={(event) => {
                  const season = event.target.value as Preferences['color_season'] || null;
                  updateField('color_season', season);
                  setPaletteSeason(season);
                }}
              >
                <option value="">{t('colorSeason.notSet')}</option>
                {([
                  'deep-winter', 'cool-winter', 'clear-winter',
                  'deep-autumn', 'warm-autumn', 'soft-autumn',
                  'light-spring', 'warm-spring', 'clear-spring',
                  'light-summer', 'cool-summer', 'soft-summer',
                ] as const).map((season) => (
                  <option key={season} value={season}>{t(`colorSeason.${season}`)}</option>
                ))}
              </select>
              {paletteSeason && (
                <ColorSeasonDialog
                  key={paletteSeason}
                  season={paletteSeason}
                  replacesColors={!!(formData.color_favorites?.length || formData.color_avoid?.length)}
                  onClose={() => setPaletteSeason(null)}
                  onApply={(palette) => {
                    void autosave.commit({ color_favorites: palette.best, color_avoid: palette.avoid }, { atomic: true });
                    setPaletteSeason(null);
                  }}
                />
              )}
              <a className="block text-sm text-muted-foreground underline" href="https://colorwise.me/" target="_blank" rel="noopener noreferrer">
                {t('colorSeason.help')}
              </a>
            </div>
          </CardContent>
        </Card>

        {/* Style Profile */}
        <Card>
          <CardHeader>
            <CardTitle>{t('styleProfile.title')}</CardTitle>
            <CardDescription>
              {t('styleProfile.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            <StyleSlider
              label={tConst('styles.casual')}
              value={formData.style_profile?.casual ?? DEFAULT_STYLE_PROFILE.casual}
              onChange={(value) => updateStyleProfile('casual', value)}
              onCommit={(value) => updateStyleProfile('casual', value, true)}
            />
            <StyleSlider
              label={tConst('styles.formal')}
              value={formData.style_profile?.formal ?? DEFAULT_STYLE_PROFILE.formal}
              onChange={(value) => updateStyleProfile('formal', value)}
              onCommit={(value) => updateStyleProfile('formal', value, true)}
            />
            <StyleSlider
              label={tConst('styles.sporty')}
              value={formData.style_profile?.sporty ?? DEFAULT_STYLE_PROFILE.sporty}
              onChange={(value) => updateStyleProfile('sporty', value)}
              onCommit={(value) => updateStyleProfile('sporty', value, true)}
            />
            <StyleSlider
              label={tConst('styles.minimalist')}
              value={formData.style_profile?.minimalist ?? DEFAULT_STYLE_PROFILE.minimalist}
              onChange={(value) => updateStyleProfile('minimalist', value)}
              onCommit={(value) => updateStyleProfile('minimalist', value, true)}
            />
            <StyleSlider
              label={tConst('styles.bold')}
              value={formData.style_profile?.bold ?? DEFAULT_STYLE_PROFILE.bold}
              onChange={(value) => updateStyleProfile('bold', value)}
              onCommit={(value) => updateStyleProfile('bold', value, true)}
            />
          </CardContent>
        </Card>

        {/* Temperature & Comfort */}
        <Card>
          <CardHeader>
            <CardTitle>{t('temperature.title')}</CardTitle>
            <CardDescription>
              {t('temperature.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <fieldset className="space-y-2">
              <legend className="font-medium">{t('activeHours.title')}</legend>
              <p className="text-sm text-muted-foreground">{t('activeHours.description')}</p>
              <div className="grid gap-4 sm:grid-cols-2">
                {(['start', 'end'] as const).map((boundary) => (
                  <div key={boundary} className="space-y-2">
                    <Label htmlFor={`active-${boundary}`}>{t(`activeHours.${boundary}`)}</Label>
                    <Select
                      value={String(boundary === 'start' ? activeStart : activeEnd)}
                      onValueChange={(value) => updateActiveHours(boundary === 'start' ? 'active_start_hour' : 'active_end_hour', Number(value))}
                    >
                      <SelectTrigger id={`active-${boundary}`} aria-invalid={activeHoursInvalid} aria-describedby={activeHoursInvalid ? 'active-hours-error' : undefined}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Array.from({ length: 24 }, (_, hour) => (
                          <SelectItem key={hour} value={String(hour)}>{`${String(hour).padStart(2, '0')}:00`}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                ))}
              </div>
              {activeHoursInvalid && <p id="active-hours-error" role="alert" className="text-sm text-destructive">{t('activeHours.invalid')}</p>}
            </fieldset>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('temperature.unit')}</Label>
                <Select
                  value={formData.temperature_unit || PREFERENCE_DEFAULTS.temperature_unit}
                  onValueChange={(v) =>
                    updateField('temperature_unit', v as 'celsius' | 'fahrenheit')
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="celsius">{t('temperature.celsius')}</SelectItem>
                    <SelectItem value="fahrenheit">{t('temperature.fahrenheit')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t('temperature.sensitivity')}</Label>
                <Select
                  value={formData.temperature_sensitivity || PREFERENCE_DEFAULTS.temperature_sensitivity}
                  onValueChange={(v) =>
                    updateField('temperature_sensitivity', v as 'low' | 'normal' | 'high')
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">{t('temperature.sensitivityOptions.low')}</SelectItem>
                    <SelectItem value="normal">{t('temperature.sensitivityOptions.normal')}</SelectItem>
                    <SelectItem value="high">{t('temperature.sensitivityOptions.high')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('temperature.layering')}</Label>
                <Select
                  value={formData.layering_preference || PREFERENCE_DEFAULTS.layering_preference}
                  onValueChange={(v) =>
                    updateField('layering_preference', v as 'minimal' | 'moderate' | 'heavy')
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="minimal">{t('temperature.layeringOptions.minimal')}</SelectItem>
                    <SelectItem value="moderate">{t('temperature.layeringOptions.moderate')}</SelectItem>
                    <SelectItem value="heavy">{t('temperature.layeringOptions.heavy')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {(() => {
                const unit = formData.temperature_unit || PREFERENCE_DEFAULTS.temperature_unit;
                const isFahrenheit = unit === 'fahrenheit';
                const { cold, hot } = TEMPERATURE_THRESHOLDS_CELSIUS;
                const coldC = formData.cold_threshold ?? cold.default;
                const hotC = formData.hot_threshold ?? hot.default;
                const display = (celsius: number) => (isFahrenheit ? Math.round(toF(celsius)) : celsius);
                return (
                  <>
                    <div className="space-y-2">
                      <Label>{t('temperature.coldThreshold', { unit: isFahrenheit ? '\u00b0F' : '\u00b0C' })}</Label>
                      <Input
                        type="number"
                        value={display(coldC)}
                        onBlur={() => saveStagedField('cold_threshold')}
                        onChange={(event) => {
                          const raw = event.target.value === '' ? display(cold.default) : parseInt(event.target.value);
                          updateField('cold_threshold', isFahrenheit ? Math.round(toCelsius(raw)) : raw, false);
                        }}
                        min={display(cold.min)}
                        max={display(cold.max)}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>{t('temperature.hotThreshold', { unit: isFahrenheit ? '\u00b0F' : '\u00b0C' })}</Label>
                      <Input
                        type="number"
                        value={display(hotC)}
                        onBlur={() => saveStagedField('hot_threshold')}
                        onChange={(event) => {
                          const raw = event.target.value === '' ? display(hot.default) : parseInt(event.target.value);
                          updateField('hot_threshold', isFahrenheit ? Math.round(toCelsius(raw)) : raw, false);
                        }}
                        min={display(hot.min)}
                        max={display(hot.max)}
                      />
                    </div>
                  </>
                );
              })()}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t('standingRules.title')}</CardTitle>
            <CardDescription>{t('standingRules.description')}</CardDescription>
          </CardHeader>
          <CardContent>
            <Label htmlFor="standing-rules">{t('standingRules.label')}</Label>
            <Textarea
              id="standing-rules"
              rows={5}
              value={(formData.standing_rules || []).join('\n')}
              onChange={(event) => {
                const rules = event.target.value.split('\n').map((rule) => rule.trim()).filter(Boolean);
                const valid = rules.length <= 20 && rules.every((rule) => rule.length <= 300);
                setStandingRulesError((previous) => previous && !valid);
                updateField('standing_rules', event.target.value.split('\n'), false);
                if (!valid) autosave.cancelPending('standing_rules');
              }}
              onBlur={(event) => saveStandingRules(event.target.value)}
              aria-invalid={standingRulesError}
              aria-describedby={standingRulesError ? 'standing-rules-error' : undefined}
            />
            {standingRulesError && <p id="standing-rules-error" role="alert" className="text-sm text-destructive">{t('standingRules.limit')}</p>}
          </CardContent>
        </Card>

        {preferences?.wash_interval_defaults && (
          <WashingSettings
            values={formData.wash_intervals ?? {}}
            defaults={preferences.wash_interval_defaults}
            onChange={(values) => updateField('wash_intervals', values)}
          />
        )}

        {/* Recommendation Settings */}
        <Card>
          <CardHeader>
            <CardTitle>{t('recommendations.title')}</CardTitle>
            <CardDescription>
              {t('recommendations.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('recommendations.defaultOccasion')}</Label>
                <Select
                  value={formData.default_occasion || PREFERENCE_DEFAULTS.default_occasion}
                  onValueChange={(v) => updateField('default_occasion', v)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {occasions.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>{t('recommendations.varietyLevel')}</Label>
                <Select
                  value={formData.variety_level || PREFERENCE_DEFAULTS.variety_level}
                  onValueChange={(v) =>
                    updateField('variety_level', v as 'low' | 'moderate' | 'high')
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="low">{t('recommendations.varietyOptions.low')}</SelectItem>
                    <SelectItem value="moderate">{t('recommendations.varietyOptions.moderate')}</SelectItem>
                    <SelectItem value="high">{t('recommendations.varietyOptions.high')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label>{t('recommendations.avoidRepeatDays')}</Label>
                <Input
                  type="number"
                  value={formData.avoid_repeat_days ?? AVOID_REPEAT_DAYS.default}
                  onChange={(event) => updateField('avoid_repeat_days', event.target.value === '' ? AVOID_REPEAT_DAYS.default : parseInt(event.target.value), false)}
                  onBlur={() => saveStagedField('avoid_repeat_days')}
                  min={AVOID_REPEAT_DAYS.min}
                  max={AVOID_REPEAT_DAYS.max}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('recommendations.preferUnderused')}</Label>
                <Select
                  value={
                    (formData.prefer_underused_items ?? PREFERENCE_DEFAULTS.prefer_underused_items)
                      ? 'yes'
                      : 'no'
                  }
                  onValueChange={(v) => updateField('prefer_underused_items', v === 'yes')}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="yes">{t('recommendations.yes')}</SelectItem>
                    <SelectItem value="no">{t('recommendations.no')}</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* AI Endpoints */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-5 w-5" />
              {t('aiEndpoints.title')}
            </CardTitle>
            <CardDescription>
              {t('aiEndpoints.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {(formData.ai_endpoints || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">
                {t('aiEndpoints.noEndpoints')}
              </p>
            ) : (
              <div className="space-y-3">
                {(formData.ai_endpoints || []).map((endpoint, index) => (
                  <div
                    key={index}
                    className={`border rounded-lg p-4 space-y-3 ${
                      !endpoint.enabled ? 'opacity-60 bg-muted/50' : ''
                    }`}
                  >
                    <div className="space-y-2">
                      {/* Header row */}
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <div className="flex flex-col shrink-0">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-5 w-5 p-0"
                              disabled={index === 0}
                              onClick={() => {
                                const updated = [...(formData.ai_endpoints || [])];
                                [updated[index - 1], updated[index]] = [updated[index], updated[index - 1]];
                                updateField('ai_endpoints', updated);
                              }}
                            >
                              <ChevronUp className="h-3 w-3" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-5 w-5 p-0"
                              disabled={index === (formData.ai_endpoints || []).length - 1}
                              onClick={() => {
                                const updated = [...(formData.ai_endpoints || [])];
                                [updated[index], updated[index + 1]] = [updated[index + 1], updated[index]];
                                updateField('ai_endpoints', updated);
                              }}
                            >
                              <ChevronDown className="h-3 w-3" />
                            </Button>
                          </div>
                          <span className="font-medium text-sm truncate">
                            {endpoint.name || `Endpoint ${index + 1}`}
                          </span>
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <Switch
                            checked={endpoint.enabled}
                            onCheckedChange={(checked) => {
                              const updated = [...(formData.ai_endpoints || [])];
                              updated[index] = { ...updated[index], enabled: checked };
                              updateField('ai_endpoints', updated);
                            }}
                          />
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-destructive"
                            onClick={() => {
                              const updated = (formData.ai_endpoints || []).filter((_, i) => i !== index);
                              updateField('ai_endpoints', updated);
                              setEndpointTests((prev) => {
                                const newTests = { ...prev };
                                delete newTests[index];
                                return newTests;
                              });
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                      {/* Status badges and test button */}
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant={endpoint.enabled ? 'default' : 'secondary'} className="text-xs">
                          {endpoint.enabled ? t('aiEndpoints.active') : t('aiEndpoints.disabled')}
                        </Badge>
                        {endpointTests[index]?.status === 'connected' && (
                          <Badge variant="outline" className="text-xs text-green-600 border-green-600">
                            {t('aiEndpoints.connected')}
                          </Badge>
                        )}
                        {endpointTests[index]?.status === 'error' && (
                          <Badge variant="outline" className="text-xs text-red-600 border-red-600">
                            {t('aiEndpoints.error')}
                          </Badge>
                        )}
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-6 text-xs ml-auto"
                          onClick={() => handleTestEndpoint(index, endpoint.url)}
                          disabled={endpointTests[index]?.status === 'testing' || !endpoint.url}
                        >
                          {endpointTests[index]?.status === 'testing' ? (
                            <Loader2 className="h-3 w-3 animate-spin mr-1" />
                          ) : null}
                          {t('aiEndpoints.testConnection')}
                        </Button>
                      </div>
                    </div>
                    {/* Test Results */}
                    {endpointTests[index]?.status === 'connected' && endpointTests[index]?.models && (
                      <div className="text-xs space-y-1 p-2 bg-green-50 dark:bg-green-950 rounded overflow-hidden">
                        <p className="font-medium text-green-700 dark:text-green-300">
                          {t('aiEndpoints.modelsAvailable', { count: endpointTests[index].models?.length ?? 0 })}
                        </p>
                        {endpointTests[index].visionModels && endpointTests[index].visionModels!.length > 0 && (
                          <p className="text-green-600 dark:text-green-400 truncate" title={endpointTests[index].visionModels?.join(', ')}>
                            {t('aiEndpoints.visionModels', { models: endpointTests[index].visionModels?.slice(0, 3).join(', ') ?? '' })}
                            {(endpointTests[index].visionModels?.length || 0) > 3 && '...'}
                          </p>
                        )}
                        {endpointTests[index].textModels && endpointTests[index].textModels!.length > 0 && (
                          <p className="text-green-600 dark:text-green-400 truncate" title={endpointTests[index].textModels?.join(', ')}>
                            {t('aiEndpoints.textModels', { models: endpointTests[index].textModels?.slice(0, 3).join(', ') ?? '' })}
                            {(endpointTests[index].textModels?.length || 0) > 3 && '...'}
                          </p>
                        )}
                      </div>
                    )}
                    {endpointTests[index]?.status === 'error' && (
                      <div className="text-xs p-2 bg-red-50 dark:bg-red-950 rounded text-red-600 dark:text-red-400 break-words">
                        {endpointTests[index].error}
                      </div>
                    )}
                    <div className="grid gap-3 sm:grid-cols-2">
                      <div className="space-y-1">
                        <Label className="text-xs">{t('aiEndpoints.fields.name')}</Label>
                        <Input
                          value={endpoint.name}
                          onBlur={() => saveStagedField('ai_endpoints')}
                          onChange={(event) => {
                            const updated = [...(formData.ai_endpoints || [])];
                            updated[index] = { ...updated[index], name: event.target.value };
                            updateField('ai_endpoints', updated, false);
                          }}
                          placeholder={t('aiEndpoints.placeholders.name')}
                          className="h-8"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">{t('aiEndpoints.fields.url')}</Label>
                        <Input
                          value={endpoint.url}
                          onBlur={() => saveStagedField('ai_endpoints')}
                          onChange={(event) => {
                            const updated = [...(formData.ai_endpoints || [])];
                            updated[index] = { ...updated[index], url: event.target.value };
                            updateField('ai_endpoints', updated, false);
                          }}
                          placeholder="http://localhost:11434/v1"
                          className="h-8"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">{t('aiEndpoints.fields.visionModel')}</Label>
                        <Input
                          value={endpoint.vision_model}
                          onBlur={() => saveStagedField('ai_endpoints')}
                          onChange={(event) => {
                            const updated = [...(formData.ai_endpoints || [])];
                            updated[index] = { ...updated[index], vision_model: event.target.value };
                            updateField('ai_endpoints', updated, false);
                          }}
                          placeholder={EXAMPLE_VISION_MODEL}
                          className="h-8"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label className="text-xs">{t('aiEndpoints.fields.textModel')}</Label>
                        <Input
                          value={endpoint.text_model}
                          onBlur={() => saveStagedField('ai_endpoints')}
                          onChange={(event) => {
                            const updated = [...(formData.ai_endpoints || [])];
                            updated[index] = { ...updated[index], text_model: event.target.value };
                            updateField('ai_endpoints', updated, false);
                          }}
                          placeholder={EXAMPLE_TEXT_MODEL}
                          className="h-8"
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <Button
                variant="outline"
                className="flex-1"
                onClick={() => {
                  const newEndpoint: AIEndpoint = {
                    name: `Endpoint ${(formData.ai_endpoints || []).length + 1}`,
                    url: 'http://localhost:11434/v1',
                    vision_model: 'moondream',
                    text_model: 'phi3:mini',
                    enabled: true,
                  };
                  updateField('ai_endpoints', [...(formData.ai_endpoints || []), newEndpoint]);
                }}
              >
                <Plus className="h-4 w-4 mr-2" />
                {t('aiEndpoints.addEndpoint')}
              </Button>

            </div>
          </CardContent>
        </Card>
        <PreviewPhotoSettings />
      </fieldset>
    </div>
  );
}
