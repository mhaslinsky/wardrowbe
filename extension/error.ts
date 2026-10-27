declare const chrome: any;
const browser = (globalThis as any).browser || chrome;
document.querySelector('[data-i18n]')!.textContent = browser.i18n.getMessage('captureFailed');
export {};
