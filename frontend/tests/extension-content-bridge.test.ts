import { expect, it, vi } from 'vitest';
it('forwards explicit draft discard and responds through the page channel', async () => {
  const sendMessage = vi.fn().mockResolvedValue({ value: { discarded: true } });
  vi.stubGlobal('browser', { runtime: { sendMessage } });
  const post = vi.spyOn(window, 'postMessage').mockImplementation(() => {});
  await import('../../extension/bridge');
  const request = { channel: 'wardrowbe-import', direction: 'request', id: 'request-id', action: 'discard', reference: 'draft-reference', owner: 'owner' };
  window.dispatchEvent(new MessageEvent('message', { source: window, origin: location.origin, data: request }));
  await vi.waitFor(() => expect(post).toHaveBeenCalledWith(expect.objectContaining({ direction: 'response', value: { discarded: true } }), location.origin));
  expect(sendMessage).toHaveBeenCalledWith(expect.objectContaining({ action: 'discard' }));
});
