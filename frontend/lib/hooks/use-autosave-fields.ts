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

type Resource<Fields> = {
  server: Partial<Fields>;
  drafts: Partial<Fields>;
  pending: Set<keyof Fields>;
  inflight: { sent: Partial<Fields>; before: Partial<Fields> } | null;
  failures: Map<keyof Fields, unknown>;
  saved: boolean;
};

export function useAutosaveFields<Fields extends object>(
  initial: Partial<Fields> | undefined,
  persist: (patch: Partial<Fields>) => Promise<Partial<Fields>>,
  onError?: (error: unknown) => void,
) {
  const resource = useRef<Resource<Fields> | null>(null);
  if (!resource.current) resource.current = {
    server: structuredClone(initial ?? {}), drafts: {}, pending: new Set(), inflight: null, failures: new Map(), saved: false,
  };
  const state = resource.current;
  const snapshot = () => {
    const dirty = Object.keys(state.drafts).length > 0;
    const busy = !!state.inflight || state.pending.size > 0;
    const status = state.failures.size ? 'error' : busy ? 'saving' : dirty ? 'idle' : state.saved ? 'saved' : 'idle';
    return { values: structuredClone({ ...state.server, ...state.inflight?.sent, ...state.drafts }), dirty, busy, status } as const;
  };
  const [view, setView] = useState(snapshot);
  const current = useRef(view.values);
  const callbacks = useRef({ persist, onError });
  callbacks.current = { persist, onError };
  const sender = useRef<Promise<void> | null>(null);
  const publish = () => { const next = snapshot(); current.current = next.values; setView(next); };
  const fields = (patch: Partial<Fields>) => Object.keys(patch) as (keyof Fields)[];
  const base = (key: keyof Fields) => state.inflight && Object.hasOwn(state.inflight.sent, key) ? state.inflight.sent[key] : state.server[key];

  useEffect(() => {
    if (!initial) return;
    for (const key of Object.keys(initial) as (keyof Fields)[]) {
      if (Object.hasOwn(state.drafts, key) || state.pending.has(key) || (state.inflight && Object.hasOwn(state.inflight.sent, key))) continue;
      state.server[key] = structuredClone(initial[key]);
    }
    const values = structuredClone({ ...state.server, ...state.inflight?.sent, ...state.drafts });
    current.current = values;
    setView((previous) => ({ ...previous, values }));
  }, [initial, state]);

  const stage = (patch: Partial<Fields>) => {
    for (const key of fields(patch)) {
      if (sameValue(current.current[key], patch[key])) continue;
      state.pending.delete(key);
      state.failures.delete(key);
      state.saved = false;
      if (sameValue(patch[key], base(key))) delete state.drafts[key];
      else state.drafts[key] = structuredClone(patch[key]);
    }
    publish();
  };

  const send = () => {
    if (state.inflight || !state.pending.size) return;
    let finish!: () => void;
    sender.current = new Promise<void>((resolve) => { finish = resolve; });
    void (async () => {
      try {
        while (state.pending.size) {
          const keys = Array.from(state.pending);
          const sent: Partial<Fields> = {};
          // Capture the rollback baseline before the request can trigger a refetch.
          const before: Partial<Fields> = {};
          for (const key of keys) {
            sent[key] = structuredClone(state.drafts[key]);
            before[key] = structuredClone(state.server[key]);
            delete state.drafts[key];
          }
          state.pending.clear();
          state.inflight = { sent, before };
          publish();
          try {
            const response = await callbacks.current.persist(structuredClone(sent));
            if (keys.some((key) => !(key in response))) throw new Error('Saved field missing');
            for (const key of keys) {
              state.server[key] = structuredClone(response[key]);
              state.failures.delete(key);
            }
            state.saved = true;
          } catch (error) {
            for (const key of keys) {
              state.server[key] = structuredClone(before[key]);
              if (!Object.hasOwn(state.drafts, key)) state.failures.set(key, error);
            }
            callbacks.current.onError?.(error);
          }
          state.inflight = null;
        }
      } finally {
        sender.current = null;
        publish();
        finish();
      }
    })();
  };

  const commit = (patch: Partial<Fields>, { atomic = false } = {}) => {
    stage(patch);
    for (const key of fields(patch)) {
      if (!atomic && sameValue(patch[key], base(key))) {
        delete state.drafts[key];
        state.pending.delete(key);
      } else {
        state.drafts[key] = structuredClone(patch[key]);
        state.pending.add(key);
        state.failures.delete(key);
      }
    }
    send();
    publish();
    return sender.current ?? Promise.resolve();
  };

  const replace = (next: Partial<Fields>) => {
    state.server = structuredClone(next);
    state.drafts = {};
    state.pending.clear();
    state.failures.clear();
    state.saved = true;
    publish();
  };

  const transform = <Key extends keyof Fields>(key: Key, convert: (value: Fields[Key] | undefined) => Fields[Key]) => {
    if (state.inflight || state.pending.size) throw new Error('Wait for saves before changing units');
    state.server[key] = convert(structuredClone(state.server[key]));
    if (Object.hasOwn(state.drafts, key)) state.drafts[key] = convert(structuredClone(state.drafts[key]));
    publish();
  };

  return { ...view, current, stage, commit, replace, transform,
    isStaged: (key: keyof Fields) => Object.hasOwn(state.drafts, key), draftKeys: () => fields(state.drafts),
    confirmed: () => structuredClone(state.server), wait: () => sender.current ?? Promise.resolve() };
}
