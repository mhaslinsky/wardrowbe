declare const chrome: any;
const browser = (globalThis as any).browser || chrome;
window.addEventListener('message', async event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.channel !== 'wardrowbe-import' || event.data?.direction !== 'request') return;
  const { id, reference, action, owner, capture } = event.data;
  if (typeof id !== 'string' || id.length > 64 || !['load', 'save', 'cancel', 'discard'].includes(action)) return;
  try {
    const reply = await browser.runtime.sendMessage({ reference, action, owner, capture });
    window.postMessage({ channel: 'wardrowbe-import', direction: 'response', id, ...reply }, location.origin);
  } catch {
    window.postMessage({ channel: 'wardrowbe-import', direction: 'response', id, error: 'bridge' }, location.origin);
  }
});
