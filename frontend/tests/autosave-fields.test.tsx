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
