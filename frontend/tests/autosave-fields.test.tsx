import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useAutosaveFields } from '@/lib/hooks/use-autosave-fields';

it('R1-1: skips an unchanged commit without protecting the field from refetch', async () => {
  const persist = vi.fn(async (patch) => patch);
  const { result, rerender } = renderHook(({ initial }) => useAutosaveFields(initial, persist), { initialProps: { initial: { unit: 'celsius' } } });
  await act(async () => { await result.current.commit({ unit: 'celsius' }); });
  expect(persist).not.toHaveBeenCalled();
  rerender({ initial: { unit: 'fahrenheit' } });
  expect(result.current.values.unit).toBe('fahrenheit');
});

it('R1-3: adopts refetches after saving and uses them as the rollback baseline', async () => {
  const persist = vi.fn(async (patch) => patch);
  const { result, rerender } = renderHook(({ initial }) => useAutosaveFields(initial, persist), { initialProps: { initial: { unit: 'celsius' } } });
  await act(async () => { await result.current.commit({ unit: 'fahrenheit' }); });
  rerender({ initial: { unit: 'celsius' } });
  expect(result.current.values.unit).toBe('celsius');
  persist.mockRejectedValueOnce(new Error('offline'));
  await act(async () => { await result.current.commit({ unit: 'fahrenheit' }); });
  expect(result.current.values.unit).toBe('celsius');
});

it('R1-5: keeps a failed field error across another field success until the failed field is edited', async () => {
  const persist = vi.fn(async (patch) => patch).mockRejectedValueOnce(new Error('offline'));
  const initial = { unit: 'celsius', variety: 'moderate' };
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  await act(async () => { await result.current.commit({ unit: 'fahrenheit' }); });
  await act(async () => { await result.current.commit({ variety: 'high' }); });
  expect(result.current.status).toBe('error');
  act(() => { result.current.stage({ unit: 'fahrenheit' }); });
  expect(result.current.status).not.toBe('error');
});

it('R1-8: copies replacement data, submitted patches and server responses', async () => {
  const cached = { rules: ['Original'] };
  const response = { rules: ['Saved'] };
  let complete!: (value: typeof response) => void;
  const persist = vi.fn(() => new Promise<typeof response>((resolve) => { complete = resolve; }));
  const { result } = renderHook(() => useAutosaveFields(cached, persist));
  act(() => { result.current.replace(cached); });
  const submitted = { rules: ['Saved'] };
  let pending!: Promise<void>;
  act(() => { pending = result.current.commit(submitted); });
  submitted.rules.push('Changed after submission');
  await act(async () => { await Promise.resolve(); complete(response); await pending; });
  expect(cached).toEqual({ rules: ['Original'] });
  expect(persist).toHaveBeenCalledWith({ rules: ['Saved'] });
  response.rules.push('Changed after response');
  expect(result.current.values.rules).toEqual(['Saved']);
});

it('rolls back a failed value after editing away and back to the in-flight value', async () => {
  const initial = { city: 'London' };
  let reject!: (error: Error) => void;
  const persist = vi.fn(() => new Promise<typeof initial>((_resolve, fail) => { reject = fail; }));
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  await act(async () => { void result.current.commit({ city: 'Paris' }); });
  act(() => { result.current.stage({ city: 'Rome' }); result.current.stage({ city: 'Paris' }); });
  await act(async () => { reject(new Error('Offline')); await result.current.wait(); });
  expect(result.current.values.city).toBe('London');
  expect(result.current.status).toBe('error');
});

it('adopts a normalized response after a draft returns to the in-flight value', async () => {
  const initial = { city: 'London' };
  let resolve!: (value: typeof initial) => void;
  const persist = vi.fn(() => new Promise<typeof initial>((complete) => { resolve = complete; }));
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  await act(async () => { void result.current.commit({ city: ' paris ' }); });
  act(() => { result.current.stage({ city: 'Rome' }); result.current.stage({ city: ' paris ' }); });
  await act(async () => { resolve({ city: 'Paris' }); await result.current.wait(); });
  expect(result.current.values.city).toBe('Paris');
});

it('retains the last committed value after an earlier failure and a no-op blur', async () => {
  const initial = { city: 'London' };
  let reject!: (error: Error) => void;
  const persist = vi.fn(async (patch) => patch).mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  await act(async () => { void result.current.commit({ city: 'Paris' }); });
  act(() => { void result.current.commit({ city: 'Rome' }); void result.current.commit({ city: 'Rome' }); });
  await act(async () => { reject(new Error('Offline')); await result.current.wait(); });
  expect(result.current.values.city).toBe('Rome');
  expect(persist).toHaveBeenLastCalledWith({ city: 'Rome' });
});

it('keeps the captured server baseline when a refetch arrives during a failing write', async () => {
  let reject!: (error: Error) => void;
  const persist = vi.fn(() => new Promise<{ city: string }>((_resolve, fail) => { reject = fail; }));
  const { result, rerender } = renderHook(({ initial }) => useAutosaveFields(initial, persist), { initialProps: { initial: { city: 'London' } } });
  await act(async () => { void result.current.commit({ city: 'Paris' }); });
  rerender({ initial: { city: 'Berlin' } });
  expect(result.current.values.city).toBe('Paris');
  await act(async () => { reject(new Error('Offline')); await result.current.wait(); });
  expect(result.current.values.city).toBe('London');
});

it('shares the queue across remounts and keeps the returning mount latest', async () => {
  const scope = { resource: 'preferences', user: 'queue-remount' };
  const initial = { city: 'London' };
  const requests: { patch: Partial<typeof initial>; resolve: (value: typeof initial) => void }[] = [];
  const persist = vi.fn((patch) => new Promise<typeof initial>((resolve) => { requests.push({ patch, resolve }); }));
  const first = renderHook(() => useAutosaveFields(initial, persist, undefined, scope));
  act(() => { void first.result.current.commit({ city: 'Paris' }); void first.result.current.commit({ city: 'Rome' }); });
  first.unmount();
  const second = renderHook(() => useAutosaveFields(initial, persist, undefined, scope));
  act(() => { void second.result.current.commit({ city: 'Berlin' }); });
  expect(requests).toHaveLength(1);
  await act(async () => { requests[0].resolve({ city: 'Paris' }); });
  expect(requests[1].patch).toEqual({ city: 'Berlin' });
  await act(async () => { requests[1].resolve({ city: 'Berlin' }); await second.result.current.wait(); });
  expect(second.result.current.confirmed()).toEqual({ city: 'Berlin' });
});

it('keeps a later confirmed replacement when an earlier request fails', async () => {
  const initial = { city: 'London' };
  let reject!: (error: Error) => void;
  const persist = vi.fn(() => new Promise<typeof initial>((_resolve, fail) => { reject = fail; }));
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  act(() => { void result.current.commit({ city: 'Paris' }); });
  act(() => { result.current.replace({ city: 'Berlin' }); });
  await act(async () => { reject(new Error('Offline')); await result.current.wait(); });
  expect(result.current.values.city).toBe('Berlin');
  expect(result.current.confirmed().city).toBe('Berlin');
});

it('keeps a queued field pending when a newer value is staged', async () => {
  const initial = { city: 'London', style: 'casual' };
  const requests: { patch: Partial<typeof initial>; resolve: (value: typeof initial) => void }[] = [];
  const persist = vi.fn((patch) => new Promise<typeof initial>((resolve) => { requests.push({ patch, resolve }); }));
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  act(() => { void result.current.commit({ city: 'Paris' }); void result.current.commit({ style: 'formal' }); result.current.stage({ style: 'sporty' }); });
  await act(async () => { requests[0].resolve({ city: 'Paris', style: 'casual' }); });
  expect(requests).toHaveLength(2);
  expect(requests[1].patch).toEqual({ style: 'sporty' });
  await act(async () => { requests[1].resolve({ city: 'Paris', style: 'sporty' }); });
});

it('waits for the previous mount queue before a returning Reset can post', async () => {
  const scope = { resource: 'preferences', user: 'reset-remount' };
  const initial = { city: 'London' };
  const requests: { resolve: (value: typeof initial) => void }[] = [];
  const persist = vi.fn(() => new Promise<typeof initial>((resolve) => { requests.push({ resolve }); }));
  const first = renderHook(() => useAutosaveFields(initial, persist, undefined, scope));
  act(() => { void first.result.current.commit({ city: 'Paris' }); void first.result.current.commit({ city: 'Rome' }); });
  first.unmount();
  const second = renderHook(() => useAutosaveFields(initial, persist, undefined, scope));
  const postReset = vi.fn();
  let reset!: Promise<void>;
  await act(async () => { reset = second.result.current.wait().then(postReset); });
  expect(postReset).not.toHaveBeenCalled();
  await act(async () => { requests[0].resolve({ city: 'Paris' }); });
  await act(async () => { requests[1].resolve({ city: 'Rome' }); await reset; });
  expect(postReset).toHaveBeenCalledOnce();
});

it('queues a conversion while saves are pending instead of throwing', async () => {
  const initial = { height: 178 };
  let finish!: (value: typeof initial) => void;
  const persist = vi.fn(() => new Promise<typeof initial>((resolve) => { finish = resolve; }));
  const { result } = renderHook(() => useAutosaveFields(initial, persist));
  act(() => { void result.current.commit({ height: 180 }); });
  let conversion!: Promise<void>;
  act(() => { conversion = result.current.transform('height', (value) => (value ?? 0) / 2); });
  await act(async () => { finish({ height: 180 }); await conversion; });
  expect(result.current.values.height).toBe(90);
});

it.each([{ resource: 'preferences', user: 'second-user' }, { resource: 'profile', user: 'first-user' }])('isolates resources with a different scope %j', async (other) => {
  const initial = { city: 'London' };
  const persist = vi.fn(async (patch) => patch);
  const first = renderHook(() => useAutosaveFields(initial, persist, undefined, { resource: 'preferences', user: 'first-user' }));
  const second = renderHook(() => useAutosaveFields(initial, persist, undefined, other));
  act(() => { first.result.current.stage({ city: 'Paris' }); });
  expect(second.result.current.values.city).toBe('London');
  await act(async () => { await second.result.current.commit({ city: 'Berlin' }); });
  expect(first.result.current.values.city).toBe('Paris');
  expect(second.result.current.confirmed().city).toBe('Berlin');
});
