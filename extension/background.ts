import { captureProduct, validateCapture, type ProductCapture } from './capture';
import { confirmedPayload } from './confirmation';
declare const chrome: any;
const browser = (globalThis as any).browser || chrome;
export const APP_ORIGIN = 'https://wardrobe.haslinsky.net';
const IMPORT_PATH = '/dashboard/considering/import';
type Draft = { capture: ProductCapture; owner: string; tab: number; item?: string; update?: ReturnType<typeof confirmedPayload>['update']; creating?: boolean };
const locks = new Set<string>();

export async function apiRequest(path: string, token: string, method = 'GET', body?: unknown) {
  const response = await fetch(`${APP_ORIGIN}/api/v1${path}`, { method, credentials: 'include', redirect: 'error',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!response.headers.get('content-type')?.includes('application/json')) throw Error('signin');
  if (!response.ok) throw Error(response.status === 401 ? 'token' : response.status === 429 ? 'rate' : response.status === 403 ? 'identity' : 'request');
  return response.json();
}
const privateSender = (sender: any) => sender.id === browser.runtime.id && sender.url === browser.runtime.getURL('options.html');
async function tokenValue() {
  const { token } = await browser.storage.local.get('token');
  if (typeof token !== 'string' || !/^wbt_[A-Za-z0-9_-]+$/.test(token)) throw Error('token');
  return token;
}
function itemResult(value: any, owner: string, item?: string) {
  if (!value || typeof value.id !== 'string' || value.user_id !== owner || (item && value.id !== item)) throw Error('response');
  return value;
}
async function openDraft(reference: string, draft: Draft) {
  const tab = await browser.tabs.create({ url: 'about:blank' });
  draft.tab = tab.id;
  await browser.storage.local.set({ [reference]: draft });
  await browser.tabs.update(tab.id, { url: `${APP_ORIGIN}${IMPORT_PATH}?draft=${reference}` });
}
export async function handleMessage(message: any, sender: any) {
  if (privateSender(sender)) {
    if (message?.action === 'configure') {
      if (typeof message.token !== 'string' || !/^wbt_[A-Za-z0-9_-]+$/.test(message.token)) throw Error('token');
      const user = await apiRequest('/users/me', message.token);
      if (typeof user.id !== 'string') throw Error('response');
      await browser.storage.local.set({ token: message.token });
      return { configured: true };
    }
    if (message?.action === 'recover') {
      const records = await browser.storage.local.get(null);
      const drafts = Object.entries(records).filter(([key, value]: [string, any]) => key.startsWith('draft-') && (value.item || value.creating));
      for (const [reference, draft] of drafts) await openDraft(reference, draft as Draft);
      return { recovered: drafts.length };
    }
  }
  const url = new URL(sender.url || 'about:blank');
  const reference = message?.reference;
  if (sender.id !== browser.runtime.id || sender.frameId !== 0 || url.origin !== APP_ORIGIN || url.pathname !== IMPORT_PATH ||
      typeof reference !== 'string' || !/^draft-[a-f0-9]{64}$/.test(reference) || url.searchParams.get('draft') !== reference) throw Error('bridge');
  const { [reference]: draft } = await browser.storage.local.get(reference) as Record<string, Draft>;
  if (!draft || draft.tab !== sender.tab?.id) throw Error('bridge');
  if (locks.has(reference)) throw Error('busy');
  locks.add(reference);
  try {
    const token = await tokenValue();
    const user = await apiRequest('/users/me', token);
    if (user.id !== draft.owner || message.owner !== draft.owner) throw Error('identity');
    if (message.action === 'load') return { capture: draft.capture, partial: !!draft.item, uncertain: !!draft.creating };
    if (message.action === 'cancel') {
      if (draft.item || draft.creating) throw Error('partial');
      await browser.storage.local.remove(reference);
      return { cancelled: true };
    }
    if (message.action !== 'save') throw Error('bridge');
    if (draft.creating) throw Error('uncertain');
    if (!draft.item) {
      const payload = confirmedPayload(message.capture);
      draft.creating = true; draft.capture = validateCapture(message.capture);
      await browser.storage.local.set({ [reference]: draft });
      const created = await apiRequest('/considering', token, 'POST', payload.create);
      const item = itemResult(created.item, draft.owner);
      if (created.state !== 'considering') throw Error('response');
      draft.item = item.id; draft.update = payload.update; draft.creating = false;
      await browser.storage.local.set({ [reference]: draft });
    }
    await apiRequest(`/items/${draft.item}`, token, 'PATCH', draft.update);
    const item = itemResult(await apiRequest(`/items/${draft.item}`, token), draft.owner, draft.item);
    for (const [key, value] of Object.entries(draft.update!.tags)) {
      if (value !== undefined && JSON.stringify(item.tags?.[key]) !== JSON.stringify(value)) throw Error('metadata');
    }
    if (draft.update!.notes !== undefined && item.notes !== draft.update!.notes) throw Error('metadata');
    const shelf = await apiRequest('/considering', token);
    if (!Array.isArray(shelf) || !shelf.some(entry => entry.state === 'considering' && entry.item?.id === draft.item && entry.item?.user_id === draft.owner)) throw Error('response');
    await browser.storage.local.remove(reference);
    return { saved: true, item: draft.item };
  } finally { locks.delete(reference); }
}

browser.runtime.onMessage.addListener((message: unknown, sender: unknown, respond: (value: unknown) => void) => {
  handleMessage(message, sender).then(value => respond({ value }), error => respond({ error: ['token', 'rate', 'identity', 'bridge', 'busy', 'partial', 'metadata', 'response', 'signin', 'uncertain'].includes(error.message) ? error.message : 'request' }));
  return true;
});
browser.action.onClicked.addListener(async (tab: any) => {
  try {
    const [{ result }] = await browser.scripting.executeScript({ target: { tabId: tab.id }, func: captureProduct });
    const capture = validateCapture(result);
    const token = await tokenValue();
    const user = await apiRequest('/users/me', token);
    if (typeof user.id !== 'string') throw Error('response');
    const reference = `draft-${Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, '0')).join('')}`;
    await openDraft(reference, { capture, owner: user.id, tab: -1 });
    await browser.action.setBadgeText({ tabId: tab.id, text: '' });
  } catch {
    await browser.action.setBadgeText({ tabId: tab.id, text: '!' });
    await browser.runtime.openOptionsPage();
  }
});
browser.tabs.onRemoved.addListener(async (tab: number) => {
  const records = await browser.storage.local.get(null);
  for (const [reference, draft] of Object.entries(records) as [string, Draft][]) {
    if (reference.startsWith('draft-') && draft.tab === tab && !draft.item && !draft.creating && !locks.has(reference)) await browser.storage.local.remove(reference);
  }
});
void browser.storage.local.setAccessLevel?.({ accessLevel: 'TRUSTED_CONTEXTS' });
