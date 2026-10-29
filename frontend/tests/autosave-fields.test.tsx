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
