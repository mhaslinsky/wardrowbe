import { beforeEach, expect, it, vi } from 'vitest';
const contains = vi.fn();
const request = vi.fn();
beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = '<form><input id="token"></form><button id="recover"></button><button id="grant"></button><p id="status"></p>';
  contains.mockResolvedValue(false); request.mockResolvedValue(true);
  vi.stubGlobal('browser', { permissions: { contains, request }, i18n: { getMessage: (key: string) => key }, runtime: { sendMessage: vi.fn() } });
});
it('checks host permission and requests it only from the grant button', async () => {
  await import('../../extension/options');
  await vi.waitFor(() => expect(contains).toHaveBeenCalledWith({ origins: ['https://wardrobe.haslinsky.net/*'] }));
  expect(request).not.toHaveBeenCalled();
  document.querySelector<HTMLButtonElement>('#grant')!.click();
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith({ origins: ['https://wardrobe.haslinsky.net/*'] }));
  expect(document.querySelector('#status')).toHaveTextContent('permissionGranted');
});
