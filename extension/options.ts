declare const chrome: any;
const browser = (globalThis as any).browser || chrome;
for (const element of Array.from(document.querySelectorAll<HTMLElement>('[data-i18n]'))) element.textContent = browser.i18n.getMessage(element.dataset.i18n);
const status = document.querySelector('#status')!;
document.querySelector('form')!.addEventListener('submit', async event => {
  event.preventDefault();
  const input = document.querySelector<HTMLInputElement>('#token')!;
  const token = input.value.trim(); input.value = '';
  const reply = await browser.runtime.sendMessage({ action: 'configure', token }).catch(() => ({ error: 'request' }));
  status.textContent = browser.i18n.getMessage(reply?.value?.configured ? 'connected' : 'setupFailed');
});
document.querySelector('#recover')!.addEventListener('click', async () => {
  const reply = await browser.runtime.sendMessage({ action: 'recover' }).catch(() => ({ error: 'request' }));
  status.textContent = browser.i18n.getMessage(reply?.error ? 'saveFailed' : reply?.value?.recovered ? 'recovered' : 'noneToRecover');
});
export {};

const host = { origins: ['https://wardrobe.haslinsky.net/*'] };
const grant = document.querySelector<HTMLButtonElement>('#grant')!;
void browser.permissions.contains(host).then((allowed: boolean) => { grant.hidden = allowed; });
grant.addEventListener('click', async () => {
  const allowed = await browser.permissions.request(host);
  grant.hidden = allowed;
  status.textContent = browser.i18n.getMessage(allowed ? 'permissionGranted' : 'permissionDenied');
});
