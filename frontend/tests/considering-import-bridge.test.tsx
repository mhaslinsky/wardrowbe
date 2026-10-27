import { StrictMode, useEffect, useState } from 'react';
import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useConsideringImport } from '@/lib/hooks/use-considering-import';
afterEach(() => vi.restoreAllMocks());
it('loads the capture once through StrictMode effect replay and ignores foreign responses', async () => {
  history.replaceState({}, '', `/?draft=draft-${'a'.repeat(64)}`);
  const requests: unknown[] = [];
  vi.spyOn(window, 'postMessage').mockImplementation((data: any) => {
    requests.push(data);
    queueMicrotask(() => {
      window.dispatchEvent(new MessageEvent('message', { source: window, origin: 'https://evil.example', data: { ...data, direction: 'response', error: 'bridge' } }));
      window.dispatchEvent(new MessageEvent('message', { source: window, origin: location.origin, data: { ...data, direction: 'response', value: { capture: { name: 'Captured shirt' } } } }));
    });
  });
  function Confirmation() {
    const bridge = useConsideringImport('owner');
    const [status, setStatus] = useState('loading');
    useEffect(() => {
      let active = true;
      bridge('load').then(result => { if (active) setStatus(result.capture?.name || 'missing'); }).catch(() => { if (active) setStatus('error'); });
      return () => { active = false; };
    }, [bridge]);
    return <p>{status}</p>;
  }
  render(<StrictMode><Confirmation /></StrictMode>);
  expect(await screen.findByText('Captured shirt')).toBeInTheDocument();
  expect(requests).toHaveLength(1);
});
