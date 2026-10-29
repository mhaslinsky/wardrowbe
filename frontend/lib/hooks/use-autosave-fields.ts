'use client';

import { useEffect, useRef, useState } from 'react';

function sameValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const leftFields = left as Record<string, unknown>;
  const rightFields = right as Record<string, unknown>;
  const keys = Object.keys(leftFields);
  return keys.length === Object.keys(rightFields).length && keys.every((key) =>
    Object.hasOwn(rightFields, key) && sameValue(leftFields[key], rightFields[key]),
  );
}

export function useAutosaveFields<Fields extends object>(
  initial: Partial<Fields> | undefined,
  persist: (patch: Partial<Fields>) => Promise<Partial<Fields>>,
) {
  const [values, setValues] = useState<Partial<Fields>>(() => structuredClone(initial ?? {}));
  const [status, setStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [busy, setBusy] = useState(false);
  const current = useRef(structuredClone(values));
  const saved = useRef(structuredClone(values));
  const revisions = useRef(new Map<keyof Fields, number>());
  const drafts = useRef(new Set<keyof Fields>());
  const failures = useRef(new Set<keyof Fields>());
  const queued = useRef(new Map<keyof Fields, { count: number; value: unknown }>());
  const tail = useRef(Promise.resolve());
  const pending = useRef(0);
  const succeeded = useRef(false);

  const publish = () => {
    setValues(structuredClone(current.current));
    setBusy(pending.current > 0);
    setStatus(failures.current.size ? 'error' : pending.current ? 'saving' : succeeded.current ? 'saved' : 'idle');
  };

  useEffect(() => {
    if (!initial) return;
    for (const key of Object.keys(initial) as (keyof Fields)[]) {
      if (drafts.current.has(key) || queued.current.has(key)) continue;
      saved.current[key] = structuredClone(initial[key]);
      current.current[key] = structuredClone(initial[key]);
    }
    setValues(structuredClone(current.current));
  }, [initial]);

  const target = (key: keyof Fields) => queued.current.has(key) ? queued.current.get(key)!.value : saved.current[key];
  const stage = (patch: Partial<Fields>) => {
    for (const key of Object.keys(patch) as (keyof Fields)[]) {
      if (sameValue(current.current[key], patch[key])) continue;
      revisions.current.set(key, (revisions.current.get(key) ?? 0) + 1);
      current.current[key] = structuredClone(patch[key]);
      failures.current.delete(key);
      if (sameValue(patch[key], target(key))) drafts.current.delete(key);
      else drafts.current.add(key);
      succeeded.current = false;
    }
    publish();
  };

  const commit = (input: Partial<Fields>) => {
    const patch = structuredClone(input);
    const keys = (Object.keys(patch) as (keyof Fields)[]).filter((key) => !sameValue(patch[key], target(key)));
    if (!keys.length) {
      for (const key of Object.keys(patch) as (keyof Fields)[]) {
        current.current[key] = structuredClone(patch[key]);
        drafts.current.delete(key);
      }
      publish();
      return tail.current;
    }
    const changed: Partial<Fields> = {};
    for (const key of keys) changed[key] = patch[key];
    stage(changed);
    const submitted = new Map(keys.map((key) => [key, revisions.current.get(key)]));
    const persistPatch = persist;
    for (const key of keys) {
      queued.current.set(key, { count: (queued.current.get(key)?.count ?? 0) + 1, value: structuredClone(changed[key]) });
      drafts.current.delete(key);
      failures.current.delete(key);
    }
    pending.current += 1;
    publish();
    // Serialize writes so an older request cannot commit after a newer one.
    tail.current = tail.current.then(async () => {
      try {
        const response = await persistPatch(changed);
        if (keys.some((key) => !(key in response))) throw new Error('Saved field missing');
        for (const key of keys) {
          saved.current[key] = structuredClone(response[key]);
          if (revisions.current.get(key) === submitted.get(key)) {
            current.current[key] = structuredClone(response[key]);
            drafts.current.delete(key);
            failures.current.delete(key);
          }
        }
        succeeded.current = true;
      } catch {
        for (const key of keys) {
          if (revisions.current.get(key) === submitted.get(key)) {
            current.current[key] = structuredClone(saved.current[key]);
            drafts.current.delete(key);
            failures.current.add(key);
          }
        }
      } finally {
        for (const key of keys) {
          const entry = queued.current.get(key)!;
          if (entry.count === 1) queued.current.delete(key);
          else entry.count -= 1;
        }
        pending.current -= 1;
        publish();
      }
    });
    return tail.current;
  };

  const replace = (next: Partial<Fields>) => {
    saved.current = structuredClone(next);
    current.current = structuredClone(next);
    drafts.current.clear();
    failures.current.clear();
    succeeded.current = true;
    publish();
  };

  const transform = <Key extends keyof Fields>(key: Key, convert: (value: Fields[Key] | undefined) => Fields[Key]) => {
    saved.current[key] = convert(structuredClone(saved.current[key]));
    current.current[key] = convert(structuredClone(current.current[key]));
    publish();
  };

  return { values, current, stage, commit, replace, transform, status, busy, dirty: drafts.current.size > 0,
    isStaged: (key: keyof Fields) => drafts.current.has(key), draftKeys: () => Array.from(drafts.current), wait: () => tail.current };
}
