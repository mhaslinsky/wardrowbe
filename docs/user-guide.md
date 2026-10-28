# Using Wardrowbe

Wardrowbe keeps photos of your clothes, suggests outfits, records what you wear, and helps you decide what to buy. Start with a few everyday pieces, then add more of your wardrobe as you go.

This guide uses the English interface names. Open your Wardrowbe address in your phone or computer's browser. Your server operator supplies the address and sign-in access.

## Sign in and get started

1. Follow the sign-in screen at your Wardrowbe address. Depending on the server, this opens a sign-in provider or continues through a sign-in proxy. Follow that provider's instructions; an email code is not required on every installation.
2. The welcome setup walks through location, style, family, and a first clothing photo. Use **Skip for now** for optional steps you want to return to later.
3. Set your location for weather-aware suggestions. Choose favorite colors, colors to avoid, and how strongly you prefer different styles. Family setup is optional.
4. Add a first item, then choose **Go to Dashboard**. You can change your preferences in **Settings**.

Sources: [sign-in page](../frontend/app/login/page.tsx), [onboarding](../frontend/messages/en/onboarding.json).

## Add and organize your clothes

### Upload photos

1. Open **Wardrobe**, then **Add Item**.
2. Use **Single Item** for one piece or **Bulk Upload** for several photos. Photograph one garment at a time, with its shape and color visible. The upload dialog accepts PNG, JPG, and HEIC.
3. For a single item, you can enter its name, brand, notes, type, and primary color. Leave the type empty to let AI detect it.
4. Keep the browser tab open until uploads finish. Uploading and AI analysis are separate steps: after upload, items can still be queued or analyzing.
5. Open **View queue** to see analysis progress and failures. If an upload fails, use **Retry** or **Retry all**. AI analysis needs a working service configured by the operator.

Bulk upload also offers a checkbox to skip AI analysis. Those items start with placeholder details that you can edit yourself.

### Find and correct an item

Search by name, filter by clothing type or favorites, and change the sort order to find recently added or less-worn pieces. **Clear filters** restores the unfiltered view.

Open an item, choose **Edit item**, correct its details, then save. Add useful fit information to **Notes**, such as “roomy through the waist” or “sleeves run short.” Correcting an incorrect type, color, or brand improves future suggestions and previews. Record garment sizing information in Notes.

The item dialog also lets you rotate or replace photos, remove a background, choose a primary image, retry AI analysis, toggle a favorite, and delete an item. Deletion asks for confirmation and cannot be undone.

### Clean up a clothing photo

**Remove background** cuts out the garment. **Undo background removal** restores the original. Background removal needs the server's local image model or a configured HTTP provider.

**Redraw photo** generates a new garment image instead of just cutting out the background. Compare the versions, check the colors and garment details, then choose **Use redrawn** or **Use original**. The original remains available. If a redraw fails, check the item before using **Retry redraw**.

You can select several wardrobe items for bulk actions, including redraw. The confirmation warns that each selected photo may incur an AI charge. Paid retries can incur another charge; include them only when you intend to retry those photos. Redraw requires an image model and API key configured by the operator.

Sources: [wardrobe page](../frontend/app/dashboard/wardrobe/page.tsx), [item dialog](../frontend/components/item-detail-dialog.tsx), [English wardrobe labels](../frontend/messages/en/wardrobe.json), [redraw controls](../frontend/components/photo-redraw-controls.tsx), [redraw service](../backend/app/services/photo_redraw_service.py).

## Get outfit ideas

### Suggest an outfit

1. Open **Suggest Outfit**. Set your location in Settings first if weather is missing.
2. Choose the occasion. To include a particular piece, open **Build around a specific piece (optional)** and choose it from your wardrobe.
3. Select **Get Suggestion**. Compare the options with **Compare All**, or look at one with **Focus View**. Read the weather information and styling advice before choosing.
4. Use **Generate 3 New Looks** or **Try Another** when you want other options. **Dismiss outfit** passes on an option.

You can use **Override weather** to try a different temperature or condition. **Reset** clears that override.

### Build around a piece with Pairings

Open a wardrobe item and choose **Find matching outfits**. Set **Number of outfits**, then **Generate Outfits**. **View Pairings** opens the results.

The **Pairings** page collects outfits built around source pieces. Filter by the source item's type to narrow the list. Open an outfit to inspect its pieces, and use **Rate This Pairing** to give feedback; **Update Rating** changes an existing rating. You can also delete a pairing you no longer need.

### Recreate an inspiration photo

1. Open **Inspiration** and choose a **Photo or screenshot** of a look you like.
2. Select **Recreate and save**. Wardrowbe matches the look against your clothes and saves the result.
3. Inspect the matched pieces and match percentages. **No match** means a part of the inspiration had no match; an **Added base layer** is shown separately.
4. Use **Open outfit** to inspect the saved outfit. You can delete a saved inspiration look after confirming.

Inspiration uses what is already in your wardrobe. Review missing matches before using the outfit.

Sources: [suggest page](../frontend/app/dashboard/suggest/page.tsx), [pairings page](../frontend/app/dashboard/pairings/page.tsx), [pairing dialog](../frontend/components/generate-pairings-dialog.tsx), [inspiration page](../frontend/app/dashboard/inspiration/page.tsx).

## Save looks and record what you wore

### Build an outfit or use your lookbook

1. In **Outfits**, choose **New Outfit**.
2. Select pieces from **Your Wardrobe**. Use the clothing slots and search to find them; review the selected pieces on **Canvas**.
3. Under **Details**, choose an occasion and give the look a name. With at least one piece and an occasion selected, **Let AI finish this** can suggest additions.
4. Choose **Save to Lookbook** to keep a reusable look, or **Wear Today** to save and log it as worn.

Use the **Lookbook**, **Worn**, **AI**, **Pairings**, and **Replacements** filters to browse your outfits. **Ready to wear** narrows results to available outfits. Search the lookbook by name. Switch between **List** and **Calendar** to browse by date.

On a saved outfit, **Wear today** logs a wear. **Save to lookbook** keeps a reusable copy where offered. An outfit that has been worn cannot be edited in place; use **Save as new** to make another version.

### Use the daily view

On the dashboard and Suggest page, **Your day** groups suggestions, saved outfits, and past outfits for the selected day. Move between dates and use **Get options** for suggestions.

Choose **I wore this** to log the selected outfit, or **Swap a piece** to replace a garment before logging. You can record today or a past day, but not a future wear. If that day is already logged, review the existing outfit rather than repeatedly submitting it. **Undo** and **Replace** let you correct a logged day; check **Washed since?** when the correction asks about intervening laundry.

When a feedback dialog asks **Did you wear this outfit?**, choose **Yes, I wore it** or **No, I wore something else**. For the latter, select the pieces you actually wore. Add a rating and optional notes to explain what worked.

**History** provides a calendar of past recommendations with occasion and status filters. It distinguishes accepted suggestions from wear records. Accepting an idea and recording that you wore it are different actions.

### Log from an outfit selfie

1. Open **Outfit selfie** and use **Take or choose a photo**. A full-length mirror photo works best.
2. Review the wardrobe pieces the AI recognized. Remove an incorrect match, use **Swap** to replace it, or **Add item** to include a missing piece.
3. Choose **Log outfit** only after the selection is right. **View outfit** opens the logged result.

The selfie matches existing wardrobe items; it does not automatically add every visible garment as a new item. If nothing matches, add or correct your wardrobe photos first. If today already has a logged outfit, review that record before trying again.

For just one piece, open it in Wardrobe and choose **Log wear**. Its item dialog shows wear history and total wears.

Sources: [outfit builder](../frontend/app/dashboard/outfits/new/page.tsx), [outfits page](../frontend/app/dashboard/outfits/page.tsx), [outfit detail](../frontend/app/dashboard/outfits/[id]/page.tsx), [daily view](../frontend/components/day-outfits.tsx), [wear corrections](../frontend/components/undo-wear-actions.tsx), [history](../frontend/app/dashboard/history/page.tsx), [selfie page](../frontend/app/dashboard/selfie/page.tsx), [feedback labels](../frontend/messages/en/suggest.json).

## Keep laundry up to date

Each item tracks wears since its last wash. At its wash interval, it needs washing and is excluded from normal outfit recommendations.

1. Open an item and edit **Wash Interval** to set the number of wears before washing. Leave it blank for the clothing type's default.
2. Use **Put in the wash** if it needs washing earlier.
3. After washing, filter Wardrobe by **Needs wash**, open the clean items, and select **Mark Washed**. This resets their wash status so they can return to suggestions.

Use the item's wash and wear history to check a surprising status. Logging what you actually wore keeps this count useful.

Sources: [item dialog](../frontend/components/item-detail-dialog.tsx), [recommendation eligibility](../backend/app/services/recommendation_service.py), [wardrobe labels](../frontend/messages/en/wardrobe.json).

## Plan a trip

1. Open **Trips**. Enter **Destination**, **Departure**, and **Return**.
2. Select **Occasions** for the trip. The selected occasions repeat in order across the days. The form allows up to 14 days.
3. Choose **Plan trip**. Open a day's date to inspect its outfit.
4. Review the **Packing list**, including quantities and how many days each item is worn. Open a piece to inspect it in Wardrobe.

Each day identifies whether its weather comes from a forecast or a climate average. Check that source when planning far ahead. Overlapping trips are rejected. Deleting a trip preserves worn days.

Trip planning needs available clothes, working AI planning, and destination weather. If it fails, check the dates and wardrobe, then ask the operator if AI or weather is unavailable.

Source: [trips page](../frontend/app/dashboard/trips/page.tsx), [trip labels](../frontend/messages/en/trips.json).

## Schedule outfits and notifications

### Set a schedule

1. Open **Notifications** and choose **Add Schedule**.
2. Pick **Day**, **Time**, and **Occasion**. Check your **Timezone** in Settings first.
3. Enable **Send notification** if you want delivery through a notification channel. You can switch notification delivery on or off for a schedule.
4. Enable **Notify day before** for an evening-before notification using tomorrow's forecast. Review the displayed notification day and time.

Review generated outfits in the daily view. Schedules can be enabled, disabled, or deleted independently of channels.

### Add a delivery channel

Under **Notification Channels**, choose **Add Channel** and a channel type:

- **ntfy Push Notifications**: use the server URL and topic agreed with the operator, and subscribe to that topic in your ntfy app. Add an access token if the server requires one; a server-configured token may be used when offered.
- **Mattermost**: supply an incoming webhook created in Mattermost.
- **Email**: enter the delivery address. The operator must configure SMTP before the app can send email.

Use **Test** to check delivery when you are ready to receive a notification. A saved channel alone does not prove delivery works. You can enable, disable, or delete channels. A channel marked **Invalid settings** must be deleted and added again.

The operator must provide a reachable ntfy server for ntfy delivery. Email delivery, including family invitation emails, requires the server's `SMTP_*` configuration. Mobile push registrations come from the mobile app. Scheduled generation also depends on the server's background jobs running.

Sources: [notifications page](../frontend/app/dashboard/notifications/page.tsx), [notification labels](../frontend/messages/en/notifications.json), [server configuration](../backend/app/config.py), [worker jobs](../backend/app/workers).

## See clothes on yourself

### Add reference photos

In Settings, open **Preview photo** and **Show photos**. Add up to three full-length views: **Front**, **Quarter turn**, and **Side**, in even light. Face the camera for the front view.

Wear fitted clothes that show your shape clearly. A swimsuit or underwear is also an option if you are comfortable using those photos. Loose clothes hide your proportions and give the image generator less information. The generator uses the build and proportions visible in your photos and any measurements you supply.

Replace or remove an angle when needed, reading the removal confirmation before continuing. Use photos you are comfortable sending to the image-generation provider configured by the operator.

### Generate and improve a preview

Open an outfit and select **See it on me**. Preview generation requires reference photos and an image model with an API key configured by the operator. A generated picture is an illustration of a look; it cannot establish how an actual garment will fit.

Use **Like** and a note to say what a preview got right. Use **Redraw** and **What should change?** to request a correction, such as a longer hem or a different shade. If the interface says **Preview is out of date**, your clothes, photos, measurements, or preferences have changed since it was made. Generate a current preview before judging the result.

Under **Preview preferences**, save up to 10 lines of 120 characters each, such as “show the whole shoe.” Set **Shirts** separately to **Tucked**, **Untucked**, or **Let each outfit decide**, then choose **Save preview preferences**.

Sources: [photo and preference settings](../frontend/components/preview-photo-settings.tsx), [outfit preview](../frontend/components/outfit-preview.tsx), [preview service](../backend/app/services/preview_service.py), [image instructions](../backend/app/prompts/outfit_preview.txt).

## Try before buying

1. Open **Considering**, then **Add considering item**.
2. Paste a **Product link** and choose **Read link**, or drop in a product screenshot. Link reading works with public pages, not signed-in store sessions. If a store blocks reading, try an image. The image form accepts JPEG, PNG, or WebP under 20 MB.
3. On **Confirm product**, review the name, brand, price, currency, selected size and color, fabric, care, and fit notes. Select the product images you want to keep, then save. Check the extracted details and fill in anything missing.
4. Once the item is ready, **Preview on me** lets you preview it alone or with selected owned pieces. **What would I wear it with?** asks for outfits using your wardrobe.
5. If you purchase it, **Bought it** moves the item into your wardrobe after confirmation. **Passed** asks for a reason and moves it to the passed shelf. **Move back** restores a passed item to Considering.

Edit shelf entries to correct the name, price, currency, or planned size. **View source** returns to the product page when a source link is available.

If a save partly succeeds, use **Retry details** to finish it. If the save status is unknown, check the Considering shelf before continuing, so you do not create a duplicate.

Sources: [Considering page](../frontend/app/dashboard/considering/page.tsx), [link and image entry](../frontend/components/considering-fallback.tsx), [product confirmation](../frontend/components/considering-import.tsx), [preview action](../frontend/components/considering-preview-action.tsx), [suggestions action](../frontend/components/considering-suggestions-action.tsx).

## Make suggestions more useful

### Preferences and fit information

In **Settings**, review these sections:

- **Location**: use **Use My Location** or enter coordinates, review the timezone, then **Save Location**. Browser permission can be denied; manual entry remains available.
- **Style Profile**, **Favorite Colors**, and **Colors to Avoid**: describe the styles and colors you want.
- **Temperature & Comfort**: choose temperature units, sensitivity, layering, and your hot and cold thresholds.
- **Recommendation Settings**: choose a default occasion, variety, repeat avoidance, and whether to prefer underused items.
- **Standing rules**: add one instruction per line, up to 20 rules of 300 characters each.
- **Body Measurements**: choose metric or imperial units, enter the measurements you know and usual clothing sizes, then **Save Measurements**.
- **Fit profile**: use **Add brand size** under **Brand sizes** to record a brand, optional line, actual size, and note. Use **Add skip entry** under **Skip list** for brands or lines that do not work for you, with a reason. Add neck, sleeve, and thigh measurements in centimeters if useful. Describe a well-fitting piece under **Reference garment**, then save.

Also correct individual clothing details and notes in Wardrobe. Personal measurements, brand sizes, and garment notes serve different purposes; a usual shirt size does not describe every brand's fit.

If Fit profile reports **Profile changed**, another save has changed the stored profile. **Discard draft and reload** replaces your draft with the current version, so preserve any changes you need before choosing it.

**AI Endpoints** is an advanced section for custom image-analysis endpoints. Without custom endpoints, the app uses server defaults. Ask the operator for a supported endpoint before changing it; **Test Connection** checks the selected endpoint.

**Access tokens** supports integrations that need account access. Create a named token only for an integration you intend to use, copy it when shown, and keep it private. It is shown only once. **Revoke** removes its access when you no longer need it.

### Review AI Learning

Open **AI Learning** after accepting or rejecting suggestions and rating outfits. It shows feedback totals, acceptance rate, style ratings, learned color and style preferences, occasion patterns, weather preferences, and combinations you tend to like.

If there is no learning data yet, give feedback first, then use **Compute Now** to process it. **Recompute** refreshes an existing profile. **New Insights** requests fresh style insights; **Dismiss** hides an insight. Review **Suggested Preference Updates**, then choose **Update Preferences** to open Settings and make the changes you want.

These summaries reflect recorded feedback. A blank section can mean there is not enough feedback yet.

Sources: [settings page](../frontend/app/dashboard/settings/page.tsx), [fit profile form](../frontend/components/fit-profile-settings.tsx), [access tokens](../frontend/components/access-token-settings.tsx), [learning page](../frontend/app/dashboard/learning/page.tsx), [English settings](../frontend/messages/en/settings.json).

## Share with a family

Family membership is optional. It enables shared outfit browsing and ratings; consider that sharing before joining.

1. Open **Family** and choose **Create Family**, or use **Join Family** with an **Invite Code** supplied by an existing family.
2. A family admin can share the code or use **Send Invite** for an email invitation. If the invitation is saved but email cannot be sent, share the code instead.
3. Admins can edit the family name, regenerate the invite code, manage member roles, remove members, and cancel pending invitations. Review confirmations before removing someone.
4. Use **Open Family Feed** to browse other members' outfits and lookbooks. Choose a rating and optional comment, then **Submit Rating**. **Update Rating** changes your feedback; you can also remove your rating.
5. Choose **Leave Family** if you no longer want membership. You can join again later with an invite code.

Sources: [family management](../frontend/app/dashboard/family/page.tsx), [family feed](../frontend/app/dashboard/family/feed/page.tsx), [family labels](../frontend/messages/en/family.json).

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
