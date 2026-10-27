import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
const records = new Map<string, any>();
const listeners: Record<string, any> = {};
const runtime = { id: 'extension-id', getURL: (path: string) => `chrome-extension://extension-id/${path}`,
  onMessage: { addListener: (listener: unknown) => { listeners.message = listener; } }, openOptionsPage: vi.fn() };
vi.stubGlobal('chrome', { runtime, storage: { local: {
  get: async (key: string | null) => key ? { [key]: structuredClone(records.get(key)) } : Object.fromEntries(records),
  set: async (data: Record<string, unknown>) => { for (const [key, value] of Object.entries(data)) records.set(key, structuredClone(value)); },
  remove: async (key: string) => { records.delete(key); }, setAccessLevel: vi.fn(),
} }, tabs: { onRemoved: { addListener: (listener: unknown) => { listeners.remove = listener; } } }, action: { onClicked: { addListener: vi.fn() } } });
let handleMessage: typeof import('../../extension/background').handleMessage;
let apiRequest: typeof import('../../extension/background').apiRequest;
beforeAll(async () => { ({ handleMessage, apiRequest } = await import('../../extension/background')); });
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
    await expect(handleMessage(message('save'), sender)).rejects.toThrow('Connection lost');
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
    records.delete('token'); await expect(handleMessage(message('load'), sender)).rejects.toThrow('token');
    for (const status of [401, 429, 403]) {
      vi.stubGlobal('fetch', vi.fn(async () => json({}, status)));
      await expect(apiRequest('/users/me', 'wbt_invalid')).rejects.toThrow(status === 401 ? 'token' : status === 429 ? 'rate' : 'identity');
    }
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>Sign in</html>', { headers: { 'content-type': 'text/html' } })));
    await expect(apiRequest('/users/me', 'wbt_invalid')).rejects.toThrow('signin');
    vi.stubGlobal('fetch', vi.fn(async () => { throw TypeError('redirect rejected'); }));
    await expect(apiRequest('/users/me', 'wbt_invalid')).rejects.toThrow('redirect rejected');
  });
});
