# Send to Wardrowbe

Build with Node 24 from the repository root:

```sh
npm --prefix extension ci
npm --prefix extension run build
```

Outputs: `extension/dist/chrome/`, `extension/dist/firefox/`, and a ZIP for each browser in `extension/dist/`. The build uses the shared reader and the existing frontend colour vocabulary. Tests run from `frontend/`: `npm test -- tests/product-capture.test.ts tests/extension-background.test.ts tests/considering-import.test.tsx tests/considering-import-bridge.test.tsx`.

## Chrome

Open `chrome://extensions`, enable Developer mode, choose Load unpacked and select `extension/dist/chrome`. Pin Send to Wardrowbe. This installation remains until removed. After a rebuild, click Reload on its extension card and reload any confirmation tabs.

## Zen or Firefox

Open `about:debugging#/runtime/this-firefox`, choose Load Temporary Add-on and select `extension/dist/firefox/manifest.json`. Pin Send to Wardrowbe. Click Reload on its debugging card after a rebuild.

This temporary installation ends when the browser restarts. Load it again after restarting. Persistent Firefox installation requires a Mozilla-signed package; the generated ZIP is unsigned. See [Firefox temporary installation](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/).

The browser manifests differ: Chrome uses a service worker; Firefox uses background scripts. See [MDN background](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/manifest.json/background).

## Connect and capture

Sign in to Wardrowbe in this browser, including Cloudflare Access. In Wardrowbe Settings, create an access token named Send to Wardrowbe. Open the extension options, paste the token and choose Connect. If extension options show Grant Wardrowbe access, click it and approve the host permission. The token stays in extension-private storage; shopping pages and confirmation pages never receive it.

Open a product page, select its size and colour, then click Send to Wardrowbe. The confirmation tab shows the captured fields and image choices. Correct missing values, choose one to five images and select Save. Cancel creates nothing. A badge showing `!` opens setup after a missing or rejected token.

If details fail after an item was created, Retry details updates that same item. Resume unfinished saves in extension options reopens retained partial saves. If the create response was lost, the extension blocks another create and asks you to check the shelf. After checking the shelf, use the discard action to remove the draft; it never deletes a shelf item.

Browser requests include its existing Access session and the application token. The extension never reads cookies. A redirect or sign-in page is a connection failure. The public Access session must be checked in the actual browser; a localhost test cannot prove it.
