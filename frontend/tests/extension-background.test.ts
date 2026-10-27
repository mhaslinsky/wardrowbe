import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
const records = new Map<string, any>();
const listeners: Record<string, any> = {};
const runtime = { id: 'extension-id', getURL: (path: string) => `chrome-extension://extension-id/${path}`,
  onMessage: { addListener: (listener: unknown) => { listeners.message = listener; } }, openOptionsPage: vi.fn() };
vi.stubGlobal('chrome', { runtime, storage: { local: {
  get: async (key: string | null) => key ? { [key]: structuredClone(records.get(key)) } : Object.fromEntries(records),
  set: async (data: Record<string, unknown>) => { for (const [key, value] of Object.entries(data)) records.set(key, structuredClone(value)); },
  remove: async (key: string) => { records.delete(key); }, setAccessLevel: vi.fn(),
} }, tabs: { create: vi.fn(), onRemoved: { addListener: (listener: unknown) => { listeners.remove = listener; } } }, scripting: { executeScript: vi.fn() }, action: { onClicked: { addListener: (listener: unknown) => { listeners.action = listener; } }, setBadgeText: vi.fn() } });
beforeAll(async () => { await import('../../extension/background'); });
async function send(message: unknown, sender: unknown) {
  return new Promise<any>(resolve => { expect(listeners.message(message, sender, resolve)).toBe(true); });
}
async function handleMessage(message: unknown, sender: unknown) {
  const reply = await send(message, sender);
  if (reply.error) throw Error(reply.error);
  return reply.value;
}
const reference = `draft-${'a'.repeat(64)}`;
const owner = 'primary-user';
const sender = { id: runtime.id, frameId: 0, url: `https://wardrobe.haslinsky.net/dashboard/considering/import?draft=${reference}`, tab: { id: 7 } };
const capture = { source_url: 'https://store.example/shirt', name: 'Shirt', images: ['https://store.example/shirt.jpg'], colors: ['blue'], sizes: ['XL'], material_detail: '80% cotton', selected_size: 'XL', selected_color: 'blue' };
const message = (action: string) => ({ reference, owner, action, capture });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
let creates = 0;
let patchFails = false;
let item: any;
let requests: { url: string; options: RequestInit }[];
beforeEach(() => {
  vi.clearAllMocks();
  records.clear(); records.set('token', 'wbt_disposable'); records.set(reference, { capture, owner, tab: 7 });
  creates = 0; patchFails = false; item = { id: 'created-item', user_id: owner, tags: {}, notes: '' }; requests = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    requests.push({ url, options });
    if (url.endsWith('/users/me')) return json({ id: owner });
    if (options.method === 'POST') { creates++; return json({ state: 'considering', item }, 201); }
    if (options.method === 'PATCH') {
      if (patchFails) return json({ detail: 'Failed' }, 500);
      Object.assign(item, JSON.parse(options.body as string)); return json(item);
    }
    return json(url.endsWith('/considering') ? [{ state: 'considering', item }] : item);
  }));
});
describe('extension draft isolation and save', () => {
  it('never exposes the token and sends credentials only to the fixed API origin', async () => {
    const loaded = await handleMessage(message('load'), sender);
    expect(loaded).toEqual({ capture, partial: false, uncertain: false });
    expect(JSON.stringify(loaded)).not.toContain('wbt_');
    expect(await handleMessage(message('save'), sender)).toEqual({ saved: true, item: 'created-item' });
    expect(creates).toBe(1); expect(records.has(reference)).toBe(false);
    expect(item.tags).toMatchObject({ material_detail: '80% cotton', size: 'XL', colors: ['blue'] });
    for (const request of requests) {
      expect(request.url).toMatch(/^https:\/\/wardrobe.haslinsky.net\/api\/v1\//);
      expect(request.options).toMatchObject({ credentials: 'include', redirect: 'error', headers: { Authorization: 'Bearer wbt_disposable' } });
    }
  });
  it('rejects a foreign origin, tab, frame, reference and mismatched account before any create', async () => {
    for (const foreign of [{ ...sender, url: sender.url.replace('wardrobe.haslinsky.net', 'evil.example') }, { ...sender, tab: { id: 8 } }, { ...sender, frameId: 1 }, { ...sender, id: 'other-extension' }]) await expect(handleMessage(message('save'), foreign)).rejects.toThrow('bridge');
    await expect(handleMessage({ ...message('save'), reference: `draft-${'b'.repeat(64)}` }, sender)).rejects.toThrow('bridge');
    await expect(handleMessage({ ...message('save'), owner: 'foreign-user' }, sender)).rejects.toThrow('identity');
    expect(creates).toBe(0);
  });
  it('cancels or closes an untouched draft without creating an item', async () => {
    expect(await handleMessage(message('cancel'), sender)).toEqual({ cancelled: true });
    expect(records.has(reference)).toBe(false); expect(creates).toBe(0);
    records.set(reference, { capture, owner, tab: 7 });
    await listeners.remove(7); expect(records.has(reference)).toBe(false);
  });
  it('retries metadata on the exact recorded item, retaining it across tab closure', async () => {
    patchFails = true;
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('request');
    expect(records.get(reference).item).toBe('created-item');
    await listeners.remove(7); expect(records.has(reference)).toBe(true);
    await expect(handleMessage(message('cancel'), sender)).rejects.toThrow('partial');
    patchFails = false;
    await handleMessage(message('save'), sender); expect(creates).toBe(1);
  });
  it('blocks duplicate submissions while the first request is in flight', async () => {
    let release!: (response: Response) => void;
    const pending = new Promise<Response>(resolve => { release = resolve; });
    vi.stubGlobal('fetch', vi.fn(() => pending));
    const first = handleMessage(message('load'), sender);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('busy');
    release(json({ id: owner })); await first; expect(creates).toBe(0);
  });
  it('retains an uncertain creation instead of creating a duplicate after a lost response', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => {
      if (options.method === 'POST') throw Error('Connection lost');
      return json({ id: owner });
    }));
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('request');
    expect(records.get(reference).creating).toBe(true);
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('uncertain');
    expect(creates).toBe(0);
  });
  it('rejects wrong-owner or wrong-state create replies and never reports success', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? json({ state: 'bought', item: { ...item, user_id: 'foreign-user' } }) : json({ id: owner })));
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('response');
    expect(records.has(reference)).toBe(true);
  });
  it('reports missing, unknown and revoked tokens, 429, Access HTML and redirects explicitly', async () => {
    records.delete('token'); expect(await send(message('load'), sender)).toEqual({ error: 'token' }); records.set('token', 'wbt_disposable');
    for (const status of [401, 429, 403]) {
      vi.stubGlobal('fetch', vi.fn(async () => json({}, status)));
      await expect(handleMessage(message('load'), sender)).rejects.toThrow(status === 401 ? 'token' : status === 429 ? 'rate' : 'identity');
    }
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Sign in</html>', { headers: { 'content-type': 'text/html' } })));
    await expect(handleMessage(message('load'), sender)).rejects.toThrow('signin');
    vi.stubGlobal('fetch', vi.fn(async () => { throw TypeError('redirect rejected'); }));
    await expect(handleMessage(message('load'), sender)).rejects.toThrow('request');
  });
  it('allows retry after a definite 429 rejection and cancel after 422', async () => {
    const original = fetch;
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => options.method === 'POST' ? json({}, 429) : original(url, options)));
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('rate');
    expect(records.get(reference).creating).toBe(false);
    vi.stubGlobal('fetch', original);
    await expect(handleMessage(message('save'), sender)).resolves.toMatchObject({ saved: true });
    expect(creates).toBe(1);
    records.set(reference, { capture, owner, tab: 7 });
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => options.method === 'POST' ? json({}, 422) : original(url, options)));
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('request');
    await expect(handleMessage(message('cancel'), sender)).resolves.toEqual({ cancelled: true });
  });
  it.each([true, false])('discards a checked partial or uncertain draft without deleting an item: %s', async partial => {
    records.set(reference, { capture, owner, tab: 7, ...(partial ? { item: 'created-item' } : { creating: true }) });
    await expect(handleMessage(message('discard'), sender)).resolves.toEqual({ discarded: true });
    expect(records.has(reference)).toBe(false);
    expect(requests.some(request => request.options.method === 'DELETE')).toBe(false);
  });
  it.each(['material_detail', 'notes'])('keeps the draft when persisted %s differs', async field => {
    const original = fetch;
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
      const response = await original(url, options);
      if (url.endsWith('/items/created-item') && options.method === 'GET') return json({ ...item, ...(field === 'notes' ? { notes: 'different' } : { tags: { ...item.tags, material_detail: 'different' } }) });
      return response;
    }));
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('metadata');
    expect(records.get(reference).item).toBe('created-item');
  });

  it('opens setup only for auth failures and a plain page for capture failures', async () => {
    const browser = (globalThis as any).chrome;
    browser.scripting.executeScript.mockRejectedValueOnce(Error('No product found'));
    await listeners.action({ id: 9 });
    expect(runtime.openOptionsPage).not.toHaveBeenCalled();
    expect(browser.tabs.create).toHaveBeenCalledWith({ url: runtime.getURL('error.html') });
    expect(browser.action.setBadgeText).not.toHaveBeenCalled();
    browser.scripting.executeScript.mockResolvedValueOnce([{ result: capture }]); records.delete('token');
    await listeners.action({ id: 9 });
    expect(runtime.openOptionsPage).toHaveBeenCalledOnce();
    expect(browser.action.setBadgeText).toHaveBeenCalledWith({ tabId: 9, text: '!' });
  });

  it.each([401, 403, 409])('clears the create marker after definite HTTP %s', async status => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? json({}, status) : json({ id: owner })));
    expect(await send(message('save'), sender)).toEqual({ error: status === 401 ? 'token' : status === 403 ? 'identity' : 'request' });
    expect(records.get(reference).creating).toBe(false);
  });
  it('retains the create marker after HTTP 500', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? json({}, 500) : json({ id: owner })));
    expect(await send(message('save'), sender)).toEqual({ error: 'request' });
    expect(records.get(reference).creating).toBe(true);
  });

});
