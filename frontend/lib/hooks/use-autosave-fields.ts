'use client';

import { useEffect, useRef, useSyncExternalStore } from 'react';

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

type Callbacks<Fields> = { persist: (patch: Partial<Fields>) => Promise<Partial<Fields>>; onError?: (error: unknown) => void };
type Job = { work: () => void; resolve: () => void; reject: (error: unknown) => void };
const resources = new Map<string, unknown>();

function createResource<Fields extends object>(initial: Partial<Fields>, fallback: { current: Callbacks<Fields> }) {
  let server = structuredClone(initial);
  let drafts: Partial<Fields> = {};
  const pending = new Set<keyof Fields>();
  const failures = new Map<keyof Fields, unknown>();
  const versions = new Map<keyof Fields, number>();
  let inflight: { sent: Partial<Fields>; before: Partial<Fields>; versions: Map<keyof Fields, number> } | null = null;
  let saved = false;
  let resetError = false;
  let resetting = false;
  let running = false;
  let sender: Promise<void> | null = null;
  const resets: { action: () => Promise<Partial<Fields>>; resolve: () => void; reject: (error: unknown) => void }[] = [];
  const conversions: Job[] = [];
  const bindings = new Set<{ current: Callbacks<Fields> }>();
  const listeners = new Set<() => void>();
  const fields = (patch: Partial<Fields>) => Object.keys(patch) as (keyof Fields)[];
  const base = (key: keyof Fields) => inflight && Object.hasOwn(inflight.sent, key) ? inflight.sent[key] : server[key];
  const writing = () => !!inflight || pending.size > 0 || resetting || resets.length > 0;
  const snapshot = () => {
    const dirty = fields(drafts).length > 0;
    const busy = writing();
    const status = failures.size || resetError ? 'error' : busy ? 'saving' : dirty ? 'idle' : saved ? 'saved' : 'idle';
    return { values: structuredClone({ ...server, ...inflight?.sent, ...drafts }), dirty, busy, status } as const;
  };
  let view = snapshot();
  const current = { current: view.values };
  const publish = () => { view = snapshot(); current.current = view.values; for (const listener of Array.from(listeners)) listener(); };
  const callbacks = () => (Array.from(bindings).at(-1) ?? fallback).current;
  const confirm = (next: Partial<Fields>) => {
    for (const key of fields(next)) { server[key] = structuredClone(next[key]); versions.set(key, (versions.get(key) ?? 0) + 1); }
  };
  const stage = (patch: Partial<Fields>) => {
    for (const key of fields(patch)) {
      if (sameValue(current.current[key], patch[key])) continue;
      failures.delete(key);
      resetError = false;
      saved = false;
      if (sameValue(patch[key], base(key)) && !pending.has(key)) delete drafts[key];
      else drafts[key] = structuredClone(patch[key]);
    }
    publish();
  };
  // Validation may cancel an unsent write while keeping its editor draft.
  const cancelPending = (...keys: (keyof Fields)[]) => { for (const key of keys) pending.delete(key); publish(); };
  const send = () => {
    if (running) return;
    running = true;
    let finish!: () => void;
    sender = new Promise<void>((resolve) => { finish = resolve; });
    void (async () => {
      try {
        while (resets.length || pending.size || conversions.length) {
          if (resets.length) {
            const job = resets.shift()!;
            resetting = true;
            publish();
            try { const response = await job.action(); server = {}; confirm(response); failures.clear(); resetError = false; saved = true; job.resolve(); }
            catch (error) { resetError = true; job.reject(error); }
            resetting = false;
            publish();
            continue;
          }
          if (!pending.size) {
            const job = conversions.shift()!;
            try { job.work(); job.resolve(); } catch (error) { job.reject(error); }
            continue;
          }
          const keys = Array.from(pending);
          const sent: Partial<Fields> = {};
          const before: Partial<Fields> = {};
          const captured = new Map<keyof Fields, number>();
          for (const key of keys) {
            sent[key] = structuredClone(drafts[key]);
            before[key] = structuredClone(server[key]);
            captured.set(key, versions.get(key) ?? 0);
            delete drafts[key];
          }
          pending.clear();
          inflight = { sent, before, versions: captured };
          publish();
          const request = callbacks();
          try {
            const response = await request.persist(structuredClone(sent));
            if (keys.some((key) => !(key in response))) throw new Error('Saved field missing');
            confirm(Object.fromEntries(keys.map((key) => [key, response[key]])) as Partial<Fields>);
            for (const key of keys) failures.delete(key);
            saved = true;
          } catch (error) {
            for (const key of keys) {
              // A later confirmed replacement must survive an older request's failure.
              if ((versions.get(key) ?? 0) !== captured.get(key)) continue;
              server[key] = structuredClone(before[key]);
              if (!Object.hasOwn(drafts, key)) failures.set(key, error);
            }
            request.onError?.(error);
          }
          inflight = null;
          publish();
        }
      } finally { running = false; sender = null; publish(); finish(); }
    })();
  };
  const commit = (patch: Partial<Fields>, { atomic = false } = {}) => {
    stage(patch);
    for (const key of fields(patch)) {
      if (!atomic && sameValue(patch[key], base(key)) && !pending.has(key)) delete drafts[key];
      else { drafts[key] = structuredClone(patch[key]); pending.add(key); failures.delete(key); }
    }
    send();
    publish();
    return sender ?? Promise.resolve();
  };
  const adopt = (next: Partial<Fields>) => {
    if (resetting || resets.length) return;
    for (const key of fields(next)) {
      if (Object.hasOwn(drafts, key) || pending.has(key) || (inflight && Object.hasOwn(inflight.sent, key))) continue;
      const patch: Partial<Fields> = {};
      patch[key] = next[key];
      confirm(patch);
    }
    publish();
  };
  const replace = (next: Partial<Fields>) => { server = {}; confirm(next); drafts = {}; pending.clear(); failures.clear(); resetError = false; saved = true; publish(); };
  const transform = <Key extends keyof Fields>(key: Key, convert: (value: Fields[Key] | undefined) => Fields[Key], complete?: () => void) => {
    const work = () => { server[key] = convert(structuredClone(server[key])); if (Object.hasOwn(drafts, key)) drafts[key] = convert(structuredClone(drafts[key])); complete?.(); publish(); };
    if (!writing()) { work(); return Promise.resolve(); }
    const result = new Promise<void>((resolve, reject) => { conversions.push({ work, resolve, reject }); });
    send();
    return result;
  };
  const reset = (action: () => Promise<Partial<Fields>>) => {
    drafts = {}; pending.clear(); resetError = false;
    const result = new Promise<void>((resolve, reject) => { resets.push({ action, resolve, reject }); });
    send(); publish(); return result;
  };
  return { current, stage, commit, replace, transform, reset, adopt, cancelPending,
    getSnapshot: () => view,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    attach: (binding: { current: Callbacks<Fields> }) => { bindings.add(binding); return () => { bindings.delete(binding); fallback = binding; }; },
    isStaged: (key: keyof Fields) => Object.hasOwn(drafts, key), draftKeys: () => fields(drafts),
    confirmed: () => structuredClone(server), wait: () => sender ?? Promise.resolve() };
}

type Store<Fields extends object> = ReturnType<typeof createResource<Fields>>;

export function useAutosaveFields<Fields extends object>(
  initial: Partial<Fields> | undefined,
  persist: (patch: Partial<Fields>) => Promise<Partial<Fields>>,
  onError?: (error: unknown) => void,
  scope?: { resource: string; user: string },
) {
  const binding = useRef({ persist, onError });
  binding.current = { persist, onError };
  const local = useRef<Store<Fields> | null>(null);
  let store: Store<Fields>;
  if (scope) {
    const key = JSON.stringify([scope.resource, scope.user]);
    if (!resources.has(key)) resources.set(key, createResource(initial ?? {}, binding));
    store = resources.get(key) as Store<Fields>;
  } else { if (!local.current) local.current = createResource(initial ?? {}, binding); store = local.current; }
  const view = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  useEffect(() => store.attach(binding), [store]);
  useEffect(() => { if (initial) store.adopt(initial); }, [initial, store]);
  return { ...view, ...store };
}
