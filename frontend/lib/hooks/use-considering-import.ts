'use client';
import { useCallback, useRef } from 'react';
import type { ConfirmedProduct } from '../extension-reader/confirmation';
export type { ProductCapture } from '../extension-reader/capture';
export type { ConfirmedProduct } from '../extension-reader/confirmation';

export function useConsideringImport(owner: string | undefined) {
  const busy = useRef(false);
  type Reply = { capture?: ConfirmedProduct; partial?: boolean; uncertain?: boolean; saved?: boolean; item?: string };
  const loading = useRef<{ key: string; promise: Promise<Reply> }>();
  return useCallback(async (action: 'load' | 'save' | 'cancel' | 'discard', capture?: ConfirmedProduct) => {
    if (!owner) throw Error('identity');
    const reference = new URL(location.href).searchParams.get('draft');
    if (!reference || !/^draft-[a-f0-9]{64}$/.test(reference)) throw Error('bridge');
    const key = `${owner}:${reference}`;
    if (action === 'load' && loading.current?.key === key) return loading.current.promise;
    if (busy.current) throw Error('busy');
    busy.current = true;
    try {
      const promise = new Promise<Reply>((resolve, reject) => {
        const id = crypto.randomUUID();
        const timeout = setTimeout(() => { window.removeEventListener('message', receive); reject(Error('bridge')); }, 120000);
        function receive(event: MessageEvent) {
          if (event.source !== window || event.origin !== location.origin || event.data?.channel !== 'wardrowbe-import' || event.data?.direction !== 'response' || event.data?.id !== id) return;
          clearTimeout(timeout); window.removeEventListener('message', receive);
          if (event.data.error) reject(Error(event.data.error)); else resolve(event.data.value);
        }
        window.addEventListener('message', receive);
        window.postMessage({ channel: 'wardrowbe-import', direction: 'request', id, reference, action, owner, capture }, location.origin);
      });
      if (action === 'load') loading.current = { key, promise };
      return await promise;
    } finally { busy.current = false; if (action === 'load') loading.current = undefined; }
  }, [owner]);
}
