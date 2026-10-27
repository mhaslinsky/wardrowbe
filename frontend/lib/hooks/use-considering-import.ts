'use client';
import { useCallback, useRef } from 'react';
import type { ConfirmedProduct } from '../../../extension/confirmation';
export type { ProductCapture } from '../../../extension/capture';
export type { ConfirmedProduct } from '../../../extension/confirmation';

export function useConsideringImport(owner: string | undefined) {
  const busy = useRef(false);
  return useCallback(async (action: 'load' | 'save' | 'cancel', capture?: ConfirmedProduct) => {
    if (busy.current || !owner) throw Error('busy');
    const reference = new URL(location.href).searchParams.get('draft');
    if (!reference || !/^draft-[a-f0-9]{64}$/.test(reference)) throw Error('bridge');
    busy.current = true;
    try {
      return await new Promise<{ capture?: ConfirmedProduct; partial?: boolean; uncertain?: boolean; saved?: boolean; item?: string }>((resolve, reject) => {
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
    } finally { busy.current = false; }
  }, [owner]);
}
