# Using Wardrowbe

Wardrowbe keeps photos of your clothes, suggests outfits, records what you wear, and helps you decide what to buy. Start with a few everyday pieces, then add more of your wardrobe as you go.

This guide uses the English interface names. Open your Wardrowbe address in your phone or computer's browser. Your server operator supplies the address and sign-in access.

## Sign in and get started

1. Follow the sign-in screen at your Wardrowbe address. Depending on the server, this opens a sign-in provider or continues through a sign-in proxy. Follow that provider's instructions; an email code is not required on every installation. A development installation instead asks for an email and display name.
2. The setup runs in this order: Welcome, Family, Location, Style, First Item. Use **Skip for now** for optional steps you want to return to later.
3. Set your location for weather-aware suggestions. Choose favorite colors, colors to avoid, and how strongly you prefer different styles. Family setup is optional.
4. Add a first item, then choose **Go to Dashboard**. You can change your preferences in **Settings**.

Sources: [sign-in page](../frontend/app/login/page.tsx), [onboarding page](../frontend/app/onboarding/page.tsx).

## Add and organize your clothes

### Upload photos

1. Open **Wardrobe**, then **Add Item**.
2. Use **Single Item** for one piece or **Bulk Upload** for several photos. Photograph one garment at a time, with its shape and color visible. Uploads accept JPEG, PNG, WebP, and HEIC/HEIF.
3. For a single item, you can enter its name, brand, notes, type, and primary color. Leave the type empty to let AI detect it.
4. Keep the browser tab open until uploads finish. Uploading and AI analysis are separate steps: after upload, items can still be queued or analyzing.
5. Open **View queue** for AI analysis progress and failures. Each failed analysis has its own **Retry** button. AI analysis needs a working service configured by the operator.

Failed uploads appear separately in the upload indicator. It offers **Retry** and **Retry all** only for retryable failures. For an oversized, unsupported, duplicate, or invalid file, address the reported problem before uploading again: resize it, choose a supported format or valid photo, or check the existing wardrobe item.

Bulk upload also offers a checkbox to skip AI analysis. Those items start with placeholder details that you can edit yourself.

### Find and correct an item

Search by name, filter by clothing type or favorites, and change the sort order to find recently added or less-worn pieces. **Clear filters** clears the clothing type, **Needs wash**, and favorites filters. It leaves search text and sort order unchanged. With only a search active, this button may not appear; clear the search field yourself.

Open an item, choose **Edit item**, correct its details, then save. Add useful fit information to **Notes**, such as “roomy through the waist” or “sleeves run short.” Correcting an incorrect type or color improves future suggestions and previews. For previews to read a garment size, put it on a separate line in Notes as `Size: M` (using the actual size).

The item dialog also lets you rotate or replace photos, remove a background, choose a primary image, retry AI analysis, toggle a favorite, and delete an item. Deletion asks for confirmation and cannot be undone.

### Clean up a clothing photo

**Remove background** cuts out the garment. **Undo background removal** restores the original. Background removal needs the server's local image model or a configured HTTP provider.

**Redraw photo** generates a new garment image and selects it automatically on success. Compare the versions in the photo gallery and check the colors and garment details. Tap **Redrawn** to switch back to the original; **Use redrawn** then selects the saved redraw again. **Use original** appears after other photo edits when the original is available. If a redraw fails, check the item before using **Retry redraw**.

You can select several wardrobe items for bulk actions, including redraw. The confirmation warns that each selected photo may incur an AI charge. Paid retries can incur another charge; include them only when you intend to retry those photos. Redraw requires an image model, an API key, and background removal configured by the operator.

Sources: [upload form](../frontend/components/add-item-dialog.tsx), [accepted image types](../frontend/lib/image-types.ts), [wardrobe page](../frontend/app/dashboard/wardrobe/page.tsx), [item dialog](../frontend/components/item-detail-dialog.tsx), [English wardrobe labels](../frontend/messages/en/wardrobe.json), [redraw controls](../frontend/components/photo-redraw-controls.tsx), [redraw service](../backend/app/services/photo_redraw_service.py).

## Get outfit ideas

### Suggest an outfit

1. Open **Suggest Outfit**. Set your location in Settings first if weather is missing.
2. Choose the occasion. To include a particular piece, open **Build around a specific piece (optional)** and choose it from your wardrobe.
3. Select **Get Suggestion**. Compare the options with **Compare All**, or look at one with **Focus View**. Read the weather information and styling advice before choosing.
4. Use **Generate 3 New Looks** or **Try Another** when you want other options. **Dismiss outfit** passes on an option.

You can use **Override weather** to try a different temperature or condition. **Reset** clears that override.

### Build around a piece with Pairings

Open a wardrobe item and choose **Find matching outfits**. Set **Number of outfits**, then **Generate Outfits**. **View Pairings** opens the results.

The **Pairings** page collects outfits built around source pieces. Filter by the source item's type to narrow the list, and tap its garment thumbnails to inspect the outfit. Accepted pairings show **Rate This Pairing** or **Update Rating**. The feedback dialog offers notes; a rating is available only when the outfit has a logged wear. You can also delete a pairing you no longer need.

### Recreate an inspiration photo

1. Set your location in **Settings** first. Open **Inspiration** and choose a **Photo or screenshot** of a look you like: JPEG, PNG, or WebP up to 10 MB.
2. Select **Recreate and save**. This requires working vision AI and current weather at your location. Wardrowbe matches the look against your clothes and saves the result.
3. Inspect the matched pieces and match percentages. **No match** means a part of the inspiration had no match; an **Added base layer** is shown separately.
4. Use **Open outfit** to inspect the saved outfit. You can delete a saved inspiration look after confirming.

Inspiration uses what is already in your wardrobe. Review missing matches before using the outfit.

Sources: [suggest page](../frontend/app/dashboard/suggest/page.tsx), [pairings page](../frontend/app/dashboard/pairings/page.tsx), [pairing dialog](../frontend/components/generate-pairings-dialog.tsx), [pairing card](../frontend/components/pairing-card.tsx), [inspiration page](../frontend/app/dashboard/inspiration/page.tsx), [inspiration service](../backend/app/services/inspiration_service.py).

## Save looks and record what you wore

### Build an outfit or use your lookbook

1. In **Outfits**, choose **New Outfit**.
2. Select pieces from **Your Wardrobe**. Use the clothing slots and search to find them; review the selected pieces on **Canvas**.
3. Under **Details**, choose an occasion and give the look a name. With an occasion and at least one piece other than underwear selected, **Let AI finish this** can suggest additions.
4. Choose **Save to Lookbook** to keep a reusable look, or **Wear Today** to save and log it as worn.

Use the **Lookbook**, **Worn**, **AI**, **Pairings**, and **Replacements** filters to browse your outfits. **Ready to wear** shows outfits made of owned pieces that do not need washing. Search the lookbook by name. Switch between **List** and **Calendar** to browse by date.

On a saved lookbook outfit, choose **Wear on…**, pick the **Wear date** (today or a past day), then select **Log wear**. For a dated or worn outfit, **Save to lookbook** creates a reusable copy. Worn outfit details hide the edit action. **Save as new** appears only if the editor detects that the outfit has been worn.

### Use the daily view

On the dashboard and Suggest page, **Your day** groups suggestions, saved outfits, and past outfits for the selected day. Move between dates and use **Get options** for suggestions.

Choose **I wore this** to log the selected outfit, or **Swap a piece** to replace a garment before logging. You can record today or a past day, but not a future wear. If that day is already logged, review the existing outfit rather than repeatedly submitting it. **Undo** and **Replace** let you correct a logged day; check **Washed since?** when the correction asks about intervening laundry.

On a suggestion, **I wore this** logs the wear for the selected day in one step. There is no separate acceptance or yes/no feedback step. To rate a worn outfit, open **History**, select its day, and choose **Rate** (or **Update** for an existing rating). Set **Overall Rating**, add optional notes, and choose **Submit**. The outfit detail page's **Add note** opens a notes-only dialog, even for worn outfits.

**History** provides a calendar of past recommendations with occasion and status filters. The rating dialog changes feedback, not which garments or date were logged. Use the daily view to correct a wear.

### Log from an outfit selfie

1. Open **Outfit selfie** and use **Take or choose a photo**. A full-length mirror photo works best.
2. Review the wardrobe pieces the AI recognized. Remove an incorrect match, use **Swap** to replace it, or **Add item** to include a missing piece.
3. Choose **Log outfit** only after the selection is right. This saves the photo with the outfit and logs the wear. **View outfit** opens the logged result and its saved photo.

The selfie matches existing wardrobe items; it does not automatically add every visible garment as a new item. If nothing matches, add or correct your wardrobe photos first. If today already has a logged outfit, review that record before trying again.

For just one piece, open it in Wardrobe and choose **Log wear**. Its item dialog shows wear history and total wears.

### Keep a photo with an outfit

Open a saved outfit from **Outfits**. Under **Photo of me wearing it**, use **Add photo** to attach a photo without matching the garments again. The selected card picture appears above the outfit’s pieces on phones and computers. The source badge sits beside the outfit details. Use **Show AI preview** to open the preview and its **Redraw**, **Comment**, and **Delete preview** controls. Before a preview is saved, use **AI preview** to open its creation controls. When the preview is the selected picture, opening its controls does not repeat the image. On the outfit page, expand **Photo of me wearing it** to manage the attached photo.

Use **Show on card** to choose the picture shown in the outfit list:

- **Automatic** uses your photo when one exists; otherwise it keeps the garment thumbnails.
- **My photo** uses the attached outfit photo.
- **AI preview** uses the saved generated preview.

If the selected picture is unavailable, the card shows garment thumbnails. **Delete photo** removes the attached photo while keeping the outfit and its AI preview. Deleting the outfit also deletes its attached photo. Family outfit cards show garment thumbnails; they do not show your attached photo or AI preview.

Sources: [outfit builder](../frontend/app/dashboard/outfits/new/page.tsx), [outfits page](../frontend/app/dashboard/outfits/page.tsx), [outfit detail](../frontend/app/dashboard/outfits/[id]/page.tsx), [daily view](../frontend/components/day-outfits.tsx), [wear corrections](../frontend/components/undo-wear-actions.tsx), [history](../frontend/app/dashboard/history/page.tsx), [selfie page](../frontend/app/dashboard/selfie/page.tsx), [outfit photo controls](../frontend/components/outfit-photo.tsx), [outfit photo storage](../backend/app/services/outfit_photo_service.py), [feedback dialog](../frontend/components/feedback-dialog.tsx), [History rating action](../frontend/components/outfit-history-card.tsx).

## Keep laundry up to date

Each item tracks wears since its last wash. At its wash interval, it needs washing and is excluded from normal outfit recommendations.

In **Settings > Washing**, choose the wears before washing for each clothing type you own: 1 to 30, or **Never**. Select **Reset** beside a type to restore its built-in default, then select **Save**. These settings apply only to your wardrobe, starting with the next wear. Past wear counts and wash status stay as recorded.

1. Open an item and edit **Wash Interval** to override its type setting. Leave it blank and save to use your type setting, including when clearing an existing override. The **Default** placeholder shows that setting.
2. Use **Put in the wash** if it needs washing earlier.
3. After washing, filter Wardrobe by **Needs wash**, open the clean items, and select **Mark Washed**. This resets their wash status so they can return to suggestions.

Use the item's wash and wear history to check a surprising status. Logging what you actually wore keeps this count useful.

Sources: [item dialog](../frontend/components/item-detail-dialog.tsx), [item updates and wash records](../backend/app/services/item_service.py), [recommendation eligibility](../backend/app/services/recommendation_service.py), [wardrobe labels](../frontend/messages/en/wardrobe.json).

## Plan a trip

1. Open **Trips**. Enter **Destination**, **Departure**, and **Return**. Departure must be today or later.
2. Select **Occasions** for the trip. The selected occasions repeat in order across the days. The form allows up to 14 days.
3. Choose **Plan trip**. Open a day's date to inspect its outfit.
4. Review the **Packing list**, including quantities and how many days each item is worn. Open a piece to inspect it in Wardrobe.

Each day identifies whether its weather comes from a forecast or a climate average. Check that source when planning far ahead. Overlapping trips are rejected. Deleting a trip preserves worn days.

Trip planning needs available clothes, working AI planning, and destination weather. If it fails, check the dates and wardrobe, then ask the operator if AI or weather is unavailable.

Sources: [trip planning service](../backend/app/services/trip_service.py), [trips page](../frontend/app/dashboard/trips/page.tsx), [trip labels](../frontend/messages/en/trips.json).

## Schedule outfits and notifications

### Set a schedule

1. Open **Notifications** and choose **Add Schedule**.
2. Pick **Day**, **Time**, and **Occasion**. Check your **Timezone** in Settings first.
3. **Send notification** defaults to on. If it is on and no enabled channel is available, the worker skips outfit generation entirely. Set up and enable a channel, or switch **Send notification** off to generate outfits without sending notifications.
4. Enable **Notify day before** to deliver on the previous day at the configured **Time**, using tomorrow's forecast when available. Choose an evening time if you prefer. Review the displayed notification day and time.

Review generated outfits in the daily view. Schedules can be enabled, disabled, or deleted independently of channels.

### Add a delivery channel

Under **Notification Channels**, choose **Add Channel** and a channel type:

- **ntfy Push Notifications**: use the server URL and topic agreed with the operator, and subscribe to that topic in your ntfy app. Add an access token if the server requires one; a server-configured token may be used when offered.
- **Mattermost**: supply an incoming webhook created in Mattermost.
- **Email**: enter the delivery address. The operator must configure SMTP before the app can send email.

Use **Test** to check delivery when you are ready to receive a notification. A saved channel alone does not prove delivery works. You can enable, disable, or delete channels. A channel marked **Invalid settings** must be deleted and added again.

The operator must provide a reachable ntfy server for ntfy delivery. Email delivery, including family invitation emails, requires the server's `SMTP_*` configuration. Scheduled generation also depends on the server's background jobs running.

Sources: [notifications page](../frontend/app/dashboard/notifications/page.tsx), [notification labels](../frontend/messages/en/notifications.json), [channel form](../frontend/components/notifications/add-channel-dialog.tsx), [delivery providers](../backend/app/services/notification_providers.py), [server configuration](../backend/app/config.py), [worker jobs](../backend/app/workers).

## See clothes on yourself

### Add reference photos

In Settings, open **Preview photo** and **Show photos**. Start with **Front**, which is required for previews. You can also add **Quarter turn** and **Side**, for up to three full-length views in even light. Face the camera for the front view.

Wear fitted clothes that show your shape clearly. A swimsuit or underwear is also an option if you are comfortable using those photos. Loose clothes hide your proportions and give the image generator less information. The generator uses the build and proportions visible in your photos, plus your height, inseam, and usual shirt, pants, and shoe sizes.

Replacing or removing the front photo deletes saved outfit previews. Replacing it happens as soon as you choose a file, with no confirmation. Removal asks for confirmation before proceeding. Use photos you are comfortable sending to the image-generation provider configured by the operator.

### Generate and improve a preview

Open an outfit and select **See it on me**. Preview generation requires reference photos and an image model with an API key configured by the operator. The preview uses your reference photos for your build, along with your height, inseam, and shirt, pants, and shoe sizes. Other measurements help with fit checks and suggestions. Use **Redraw** to adjust the build to better match your photos. A generated picture is an illustration of a look; it cannot establish how an actual garment will fit.

When the outfit has a real photo and an AI preview, select **Compare with my photo** and confirm the paid call. The visible differences fill **What should change?**. Edit the note, then select **Redraw**. Comparison never runs automatically. The photo-informed flag belongs to each feedback entry; a later comment on the same AI call does not carry it.

Use **Comment** and the **Comments** field to say what worked and what did not, then press **Save**. The comment appears below the preview controls. Clear the field and save to remove it. Use **Redraw** and **What should change?** to request a correction, such as a longer hem or a different shade. If the interface says **Preview is out of date**, your clothes, photos, measurements, or preferences have changed since it was made. Generate a current preview before judging the result.

Under **Preview preferences**, save up to 10 lines of 120 characters each, such as “show the whole shoe.” Set **Shirts** separately to **Tucked**, **Untucked**, or **Let each outfit decide**, then choose **Save preview preferences**.

Sources: [photo and preference settings](../frontend/components/preview-photo-settings.tsx), [outfit preview](../frontend/components/outfit-preview.tsx), [preview service](../backend/app/services/preview_service.py), [image instructions](../backend/app/prompts/outfit_preview.txt).

## Try before buying

1. Open **Considering**, then **Add considering item**.
2. Paste a **Product link** and choose **Read link**, or drop in a product screenshot. Link reading works with public pages, not signed-in store sessions. If a store blocks reading, try an image. The image form accepts JPEG, PNG, or WebP up to 20 MB.
3. On **Confirm product**, review the name, brand, price, currency, selected size and color, fabric, care, and fit notes. Select the product images you want to keep, then save. Check the extracted details and fill in anything missing.
4. Once the item is ready, **Preview on me** lets you preview it alone or with selected owned pieces. **What would I wear it with?** asks for outfits using your wardrobe.
5. If you purchase it, **Bought it** moves the item into your wardrobe after confirmation. **Passed** asks for a reason and moves it to the passed shelf. **Move back** restores a passed item to Considering.

Edit shelf entries to correct the name, price, currency, or planned size. **View source** returns to the product page when a source link is available.

The optional Send to Wardrowbe browser extension can capture a product from its store page. Ask the operator for an installation built from this fork and follow the [extension setup instructions](../extension/INSTALL.md); it needs an access token and sign-in in the same browser.

If a save partly succeeds, use **Retry details** to finish it. If the save status is unknown, check the Considering shelf before continuing, so you do not create a duplicate.

Sources: [Considering page](../frontend/app/dashboard/considering/page.tsx), [link and image entry](../frontend/components/considering-fallback.tsx), [product confirmation](../frontend/components/considering-import.tsx), [preview action](../frontend/components/considering-preview-action.tsx), [suggestions action](../frontend/components/considering-suggestions-action.tsx).

### Read fit evidence through the API

Signed-in integrations can request `GET /api/v1/considering/{id}/fit-evidence` for a ready, active item on their own Considering shelf. The response includes brand-size and skip-list matches, fabric comparisons with owned pieces, explicit ratings and feel notes, possible duplicates, and sampled outfit ideas.

Keep **Fit profile** current and record item notes with `Rating:`, `Size:`, `Fabric:`, `Fabric detail:`, `Fit:`, `Warmth:`, `Line:`, `Color:`, `Works:`, `Doesnt:`, or `Style:`. Ratings accept `love`, `like`, `mixed`, and `dislike`. Missing information stays unknown; a shared fiber alone does not establish comfort or fit. Outfit ideas ignore weather and laundry, and do not test every possible combination.

Each fact cites its item or fit-profile row. Conflicting sizes yield no recommendation. The endpoint makes no AI calls and saves no outfits or wear history. Items return 404 if they are unready, missing, archived, bought, passed, or owned by another user.

Fabric notes without percentages appear as quoted note text in `feel.fiber_notes`. Conflicts read as "can't tell" and leave the composition unresolved.

Fiber comparisons require complete explicit compositions; stored material is context only. Outfit results separate `pool_truncated` from `idea_limit_truncated`.

Feel excerpts match only when their sets are identical and nonempty; other prose comparisons stay unresolved. Feel status excludes brand, line, type, and subtype comparisons.

Source: [fit evidence](../backend/app/services/fit_evidence_service.py).

### Request a buying check through the API

On **Considering**, press **Check** under **Should I buy this?** on an item card. The check runs only when you press the button. It shows the model's estimated chance you will like the item, followed by **Pluses**, **Minuses**, **Unknowns**, and **Outfits it completes**. Each reason names its dimension and links to the cited owned item or **Fit profile**, with your quoted note. The item under consideration appears by name without a wardrobe link. **Outfits it completes** shows the number of ideas, then the first three. Each line lists only the owned items, joined with +, and links to each one. **Show all** opens the full list and **Show fewer** closes it. A notice identifies sampled results when the full list is visible.

A skip-list match, wrong size, or exact duplicate shows a reason instead of a percentage. A near-duplicate warning appears above the estimate. **Not enough to go on yet.** means there is too little evidence for an estimate. **Check is not set up.** means the service is disabled or lacks its key. If you see **Try again later.**, press **Check** again later. Editing the item or planned size clears the result; press **Check** for a new estimate.

Signed-in integrations can also request `POST /api/v1/considering/{id}/buy-check`. The response gives the decision model's estimated percentage, cited pluses and minuses, unknowns, sampled outfits, the model used, and any fallback reason. Skip-list matches, known-wrong sizes, and exact duplicates replace the percentage with a reason. Too few close rated items or conflicting experience produces no estimate. Price is excluded from the request.

The operator configures `WARDROWBE_OPENROUTER_API_KEY`, `BUY_CHECK_PRIMARY_MODEL` (default `baseten/elephant-1:free`), and `BUY_CHECK_FALLBACK_MODEL` (default `typesafe/jev-1.14`). Unavailable or invalid primary answers trigger the fallback. Each attempt is recorded in AI calls. Fallback notices use your enabled ntfy channel, at most once per day; failed notices can retry on the next check.

The response names the requested model separately from the returned model ID, which is limited to 120 characters. Disabling internal AI or text AI returns `error: disabled` without a percentage or provider request.

Feel and quality reasons use Fabric detail and Warmth notes. Pluses cite liked or loved garments; minuses cite mixed or disliked garments, or a Doesnt note for fit. Only notes with text support these reasons. A model direction without supporting experience stays unknown. A low-outfit-count minus cites the count as structured evidence.

## Make suggestions more useful

### Preferences and fit information

In **Settings**, review these sections. Use **Save** for general preference changes; location, measurements, and preview preferences have their own save controls:

- **Location**: use **Use My Location** or enter coordinates, review the timezone, then **Save Location**. Browser permission can be denied; manual entry remains available.
- **Style Profile**, **Favorite Colors**, and **Colors to Avoid**: describe the styles and colors you want.
- **Temperature & Comfort**: choose temperature units, sensitivity, layering, and your hot and cold thresholds.
- **Washing**: set wears before washing by clothing type; see [laundry](#keep-laundry-up-to-date).
- **Recommendation Settings**: choose a default occasion, variety, repeat avoidance, and whether to prefer underused items.
- **Standing rules**: add one instruction per line, up to 20 rules of 300 characters each.
- **Body Measurements**: choose metric or imperial units, enter the measurements you know and usual clothing sizes, then **Save Measurements**.

Open **Fit profile** from the left menu or the link in **Settings**. Use **Add brand size** under **Brand sizes** to record a brand, optional line, actual size, and note. Use **Add skip entry** under **Skip list** for brands or lines that do not work for you, with a reason. Add neck, sleeve, and thigh measurements in centimeters if useful. Describe a well-fitting piece under **Reference garment**, then save.

Also correct individual clothing details and notes in Wardrobe. Personal measurements, brand sizes, and garment notes serve different purposes; a usual shirt size does not describe every brand's fit.

If Fit profile reports **Profile changed**, another save has changed the stored profile. **Discard draft and reload** replaces your draft with the current version, so preserve any changes you need before choosing it.

**AI Endpoints** is an advanced section for custom image-analysis endpoints. Without custom endpoints, the app uses server defaults. Ask the operator for a supported endpoint before changing it; **Test Connection** checks the selected endpoint.

**Access tokens** supports integrations that need account access. Create a named token only for an integration you intend to use, copy it when shown, and keep it private. It is shown only once. **Revoke** removes its access when you no longer need it.

### Choose a color season

In **Settings**, find **Color season** beside **Favorite Colors** and **Colors to Avoid**. Use **Find your season on ColorWise.** to take the ColorWise analysis, then return, select the matching choice, such as **Cool Winter**, and choose **Save**. Each person sets their own season.

Suggestions gently favor your season's colors for pieces worn near your face, such as tops, layers, coats, dresses, hats, and scarves. Bottoms and shoes are unaffected. Your explicit favorite and avoid colors take priority over the season; learned color preferences still contribute when scoring applies.

Selecting a season opens a preview of its favorite and avoid colors. Choose **Use these colors** to replace both lists, or **Not now** to keep your current picks, then choose **Save**.

Choose **Not set** and **Save** to stop applying a color season.

Changing style sliders keeps your saved color season. Only changing **Color season** updates that preference.

If you change **Color season** during a save, choose **Save** again after it finishes to save the new choice.

Sources: [Settings](../frontend/app/dashboard/settings/page.tsx), [season palettes](../backend/app/data/color_seasons.py), [item scoring](../backend/app/services/item_scorer.py), [suggestion preferences](../backend/app/services/recommendation_service.py).

### AI call records

AI results and feedback are recorded to improve the app. These records keep prompt and image sizes and hashes, structured answers, and provider usage when available. Plain-text answers are kept only as sizes and hashes. Preview comments and redraw notes refer to the image you judged; outfit ratings and comments follow the source AI call. Older results and dropped recordings can have no linked call. Call records are deleted with your account.

Signed-in integrations can read their own records with `GET /api/v1/ai-calls` and `GET /api/v1/ai-calls/{id}`. The list accepts `limit` (1 to 100) and returns `next_cursor` for the next page. Pass it back as `cursor`. Family membership does not grant access to another person's call records or private feedback.

Operators can export all users' call records and linked feedback on the server:

```sh
docker compose exec backend python -m app.cli.export_ai_calls --output /data/wardrobe/ai-calls.jsonl
```

Choose a new path in an existing directory. The command creates a mode-0600 JSON Lines file, reports an explicit row count, and fails without overwriting an existing file. Each line has `schema_version: 1`. Export includes family ratings with their author's ID; owner API responses exclude feedback authored by other people.

**Bought it** and **Passed** feedback links to the latest successful `pairing` or `considering_preview` call and, independently, the latest successful `buy_check` call for the owned item. Each call must precede the decision's last update. Failed attempts, tagging, redraws, older calls and calls made at or after the decision receive none. Both the owner API and the export include these links.

Saving or wearing a copy of a suggested outfit adds feedback to the original call, so one call can carry several outfit feedback rows.

Operator note: graceful shutdown allows five seconds for writes and one for cleanup; an abrupt stop can lose pending records.

### Review AI Learning

Open **AI Learning** after logging wears, dismissing suggestions, and rating outfits. It shows feedback totals, acceptance rate, style ratings, learned color and style preferences, occasion patterns, weather preferences, and combinations you tend to like.

If there is no learning data yet, give feedback first, then use **Compute Now** to process it. **Recompute** refreshes an existing profile. When existing insights are shown, **New Insights** requests fresh style insights; **Dismiss** hides an insight. Review **Suggested Preference Updates**, then choose **Update Preferences** to open Settings and make the changes you want.

These summaries reflect recorded feedback. A blank section can mean there is not enough feedback yet.

Sources: [settings page](../frontend/app/dashboard/settings/page.tsx), [fit profile page](../frontend/app/dashboard/fit-profile/page.tsx), [fit profile form](../frontend/components/fit-profile-settings.tsx), [access tokens](../frontend/components/access-token-settings.tsx), [learning page](../frontend/app/dashboard/learning/page.tsx), [English settings](../frontend/messages/en/settings.json).

## Share with a family

Family membership is optional. It enables shared outfit browsing and ratings; consider that sharing before joining.

1. Open **Family** and choose **Create Family**, or use **Join Family** with an **Invite Code** supplied by an existing family.
2. A family admin can share the code or use **Send Invite** for an email invitation. If the invitation is saved but email cannot be sent, share the code instead.
3. Admins can edit the family name, regenerate the invite code, manage member roles, remove members, and cancel pending invitations. Review confirmations before removing someone.
4. Use **Open Family Feed** to browse other members' dated outfits. Undated lookbook templates are not included. Open the rating form with the member's Rate button, or **Edit** for an existing rating. Choose a rating and optional comment, then **Submit Rating** or **Update Rating**. You can also remove your rating.
5. Before choosing **Leave Family**, a sole admin with other members must make another member an admin or remove all other members. Then confirm leaving. Removing all other members and then leaving deletes the family; its old invite code cannot restore it. You can rejoin later with a valid invite code only if the family still exists.

Sources: [family leave endpoint](../backend/app/api/families.py), [outfit filtering](../backend/app/services/outfit_service.py), [family management](../frontend/app/dashboard/family/page.tsx), [family feed](../frontend/app/dashboard/family/feed/page.tsx), [family labels](../frontend/messages/en/family.json).

## Read your wardrobe statistics

**Analytics** shows item totals, outfits generated, acceptance rate, and total wears. Color and clothing-type charts describe your wardrobe's mix. **Most Worn**, **Least Worn**, and **Never Worn** help you find favorites and overlooked pieces. **Acceptance Rate Trend** shows how your responses change over time.

Wear statistics depend on logging wears, and acceptance statistics depend on responding to suggestions. Empty charts are expected before you have that activity. Use the figures to see which items get regular use.

Source: [analytics page](../frontend/app/dashboard/analytics/page.tsx).

## When something does not work

- Missing weather: review your location and timezone in Settings.
- Incomplete item details: check the analysis queue, then edit the item or retry analysis.
- No useful outfit: add missing basics and footwear, correct tags, check laundry status, and review the occasion and preferences.
- Missing notifications: check the channel, schedule, delivery switch, and timezone. Ask the operator to check SMTP, ntfy, or background jobs as appropriate.
- Unavailable AI or image generation: ask the operator to check the server's AI service and image-model configuration. Text, vision analysis, and image generation have separate requirements.
- Failed product link: try a screenshot instead.

When reporting a problem, include the page, what you selected, the visible error, and roughly when it happened. Avoid sharing access tokens or private photos in a public issue.

Operator setup: [AI configuration and notifications](../README.md#configuration), [server settings](../backend/app/config.py).

## Photo access

Sign in to view photos. A copied photo link works only for someone with access to that photo. Family members can view item photos. Reference photos, AI previews, outfit selfies, and inspiration photos are visible only to their owner.

Sources: [item photo access](../backend/app/api/images.py), [preview and selfie access](../backend/app/api/users.py), [inspiration photo access](../backend/app/api/inspiration.py), [browser photo route](../frontend/app/photos/[...path]/route.ts).
