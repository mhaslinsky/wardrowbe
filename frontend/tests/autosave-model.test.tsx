import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import { useAutosaveFields } from '@/lib/hooks/use-autosave-fields';

type Fields = Record<'alpha' | 'beta' | 'gamma', string>;
const keys = ['alpha', 'beta', 'gamma'] as const;

it.each([11, 286, 104729])('seed %i preserves the server, draft and in-flight model after every event', async (seed) => {
  let randomState = seed;
  const random = (limit: number) => { randomState = (Math.imul(randomState, 1664525) + 1013904223) >>> 0; return Math.floor(randomState / 0x1_0000_0000 * limit); };
  let server: Fields = { alpha: '0', beta: '0', gamma: '0' };
  let stored = { ...server };
  const drafts: Partial<Fields> = {};
  const pending = new Set<keyof Fields>();
  let revisions = { alpha: 0, beta: 0, gamma: 0 };
  let flight: { sent: Partial<Fields>; baseline: Fields; revisions: typeof revisions } | null = null;
  let request: { patch: Partial<Fields>; committed: Fields; resolve: (value: Fields) => void; reject: (error: Error) => void } | null = null;
  let committed = { ...server };
  const events = new Set<string>();
  const protocolErrors: string[] = [];
  const persist = (patch: Partial<Fields>) => new Promise<Fields>((resolve, reject) => {
    if (!flight) start();
    if (request) protocolErrors.push('Parallel requests');
    for (const key of Object.keys(patch) as (keyof Fields)[]) {
      if (patch[key] !== committed[key]) protocolErrors.push('Sent an uncommitted value');
    }
    request = { patch: structuredClone(patch), committed: { ...committed }, resolve, reject };
  });
  const scope = { resource: 'model', user: `lifecycle-${seed}` };
  const mount = () => renderHook(({ initial }) => useAutosaveFields<Fields>(initial, persist, undefined, scope), { initialProps: { initial: { ...stored } } });
  let mounted = mount();
  const display = () => ({ ...server, ...flight?.sent, ...drafts });
  const base = (key: keyof Fields) => flight && key in flight.sent ? flight.sent[key] : server[key];
  const start = () => {
    if (flight || !pending.size) return;
    const sent: Partial<Fields> = {};
    for (const key of Array.from(pending)) { sent[key] = drafts[key]; delete drafts[key]; }
    pending.clear();
    flight = { sent, baseline: { ...server }, revisions: { ...revisions } };
  };
  const edit = (key: keyof Fields, value: string) => {
    if (display()[key] === value) return;
    const queued = pending.has(key);
    if (queued) committed[key] = value;
    if (value === base(key) && !queued) delete drafts[key];
    else drafts[key] = value;
  };
  const commit = (key: keyof Fields, value: string) => {
    edit(key, value);
    committed[key] = value;
    if (value !== base(key) || pending.has(key)) { drafts[key] = value; pending.add(key); }
    else { delete drafts[key]; pending.delete(key); }
    start();
  };
  const inflightContains = (key: keyof Fields) => flight !== null && key in flight.sent;
  const check = (step: number) => {
    expect(protocolErrors, `protocol seed=${seed} step=${step}`).toEqual([]);
    expect(mounted.result.current.values, `seed=${seed} step=${step}`).toEqual(display());
    expect(request?.patch ?? null, `request seed=${seed} step=${step}`).toEqual(flight?.sent ?? null);
  };
  const settle = async (failed: boolean) => {
    if (!flight || !request) return;
    events.add(failed ? 'failure' : 'success');
    const sent = flight.sent;
    const active = request;
    const previousStored = { ...stored };
    request = null;
    for (const field of Object.keys(sent) as (keyof Fields)[]) {
      if (failed) { if (revisions[field] === flight.revisions[field]) server[field] = flight.baseline[field]; }
      else { server[field] = sent[field]!; revisions[field] += 1; }
    }
    if (!failed) stored = { ...stored, ...active.patch };
    flight = null;
    await act(async () => { if (failed) active.reject(new Error('Offline')); else active.resolve({ ...stored }); });
    start();
    if (failed) expect(stored).toEqual(previousStored);
    else for (const key of Object.keys(active.patch) as (keyof Fields)[]) {
      expect(stored[key]).toBe(active.committed[key]);
      expect(stored[key]).toBe(sent[key]);
    }
  };
  try {
    for (let step = 0; step < 180; step++) {
      const key = keys[random(keys.length)];
      const value = String(random(4));
      const event = random(8);
      if (event === 0) {
        if (display()[key] !== value) events.add('edit');
        edit(key, value);
        await act(async () => { mounted.result.current.stage({ [key]: value }); });
      } else if (event === 1 || event === 2) {
        const next = event === 2 ? display()[key] : value;
        events.add('commit');
        if (event === 2 && next === base(key)) events.add('blurNoop');
        commit(key, next);
        await act(async () => { void mounted.result.current.commit({ [key]: next }); });
      } else if (event === 3 || event === 4) {
        await settle(event === 4);
      } else if (event === 6) {
        events.add('remount');
        mounted.unmount();
        await act(async () => { mounted = mount(); });
      } else if (event === 7 && flight) {
        events.add('confirmation');
        stored = { alpha: value, beta: value, gamma: value };
        server = { ...stored }; committed = { ...stored };
        revisions = { alpha: revisions.alpha + 1, beta: revisions.beta + 1, gamma: revisions.gamma + 1 };
        for (const field of keys) delete drafts[field];
        pending.clear();
        act(() => { mounted.result.current.replace(stored); });
        check(step);
        await settle(true);
      } else {
        events.add('refetch');
        const incoming = { ...stored };
        for (const field of keys) {
          if (inflightContains(field)) incoming[field] = `stale-${random(3)}`;
          if (!(field in drafts) && !pending.has(field) && !inflightContains(field)) server[field] = incoming[field];
        }
        await act(async () => { mounted.rerender({ initial: incoming }); });
      }
      check(step);
    }
    for (const key of keys) {
      const next = display()[key];
      commit(key, next);
      await act(async () => { void mounted.result.current.commit({ [key]: next }); });
      check(180);
    }
    expect(events).toEqual(new Set(['edit', 'commit', 'blurNoop', 'success', 'failure', 'refetch', 'remount', 'confirmation']));
    while (flight) { await settle(false); check(180); }
    expect(mounted.result.current.values).toEqual(stored);
    expect(stored).toEqual(committed);
  } finally { mounted.unmount(); }
});
