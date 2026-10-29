'use client';

import { useEffect, useRef, useState } from 'react';

export function useAutosaveFields<Fields extends object>(
  initial: Partial<Fields> | undefined,
  persist: (patch: Partial<Fields>) => Promise<Partial<Fields>>,
) {
  const [values, setValues] = useState<Partial<Fields>>(initial ?? {});
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [busy, setBusy] = useState(false);
  const current = useRef(values);
  const saved = useRef<Partial<Fields>>(initial ?? {});
  const revisions = useRef(new Map<keyof Fields, number>());
  const tail = useRef(Promise.resolve());
  const pending = useRef(0);
  const save = useRef(persist);
  save.current = persist;

  useEffect(() => {
    if (!initial) return;
    const untouched = Object.fromEntries(
      Object.entries(initial).filter(([key]) => !revisions.current.has(key as keyof Fields)),
    ) as Partial<Fields>;
    saved.current = { ...initial, ...saved.current, ...untouched };
    current.current = { ...current.current, ...untouched };
    setValues(current.current);
  }, [initial]);

  const stage = (patch: Partial<Fields>) => {
    for (const key of Object.keys(patch) as (keyof Fields)[]) {
      revisions.current.set(key, (revisions.current.get(key) ?? 0) + 1);
    }
    current.current = { ...current.current, ...patch };
    setValues(current.current);
    setStatus(pending.current ? 'saving' : 'idle');
  };

  const commit = (patch: Partial<Fields>) => {
    stage(patch);
    const keys = Object.keys(patch) as (keyof Fields)[];
    const submitted = new Map(keys.map((key) => [key, revisions.current.get(key)]));
    const persistPatch = save.current;
    pending.current += 1;
    setBusy(true);
    setStatus('saving');
    // Serialize writes so an older request cannot commit after a newer one.
    tail.current = tail.current.then(async () => {
      try {
        const response = await persistPatch(patch);
        if (keys.some((key) => !(key in response))) throw new Error('Saved field missing');
        for (const key of keys) {
          saved.current[key] = response[key];
          if (revisions.current.get(key) === submitted.get(key)) {
            current.current = { ...current.current, [key]: saved.current[key] };
          }
        }
        setValues(current.current);
        setStatus(pending.current > 1 ? 'saving' : 'saved');
      } catch {
        for (const key of keys) {
          if (revisions.current.get(key) === submitted.get(key)) {
            current.current = { ...current.current, [key]: saved.current[key] };
          }
        }
        setValues(current.current);
        setStatus('error');
      } finally {
        pending.current -= 1;
        setBusy(pending.current > 0);
      }
    });
    return tail.current;
  };

  const replace = (next: Partial<Fields>) => {
    saved.current = next;
    current.current = next;
    revisions.current.clear();
    setValues(next);
    setStatus('saved');
  };

  const transform = <Key extends keyof Fields>(key: Key, convert: (value: Fields[Key] | undefined) => Fields[Key]) => {
    saved.current = { ...saved.current, [key]: convert(saved.current[key]) };
    const patch: Partial<Fields> = {};
    patch[key] = convert(current.current[key]);
    stage(patch);
  };

  return { values, current, stage, commit, replace, transform, status, busy, wait: () => tail.current };
}
