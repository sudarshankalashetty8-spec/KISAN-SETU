# KisanSetu Farmer Procurement Portal

## Updated features
- Farmer registration and farmer login
- Farmer dashboard, slot booking, live queue and payment/procurement tracking
- Public Admin page removed from the main website navigation
- Separate protected Admin Login at `/admin-login`
- Admin dashboard/actions require admin authentication
- Language selector in the top-right corner
- Languages: English, Hindi, Kannada, Marathi, Telugu and Tamil
- Selected language is remembered in the browser

## Admin login
Default credentials in `app.py`:
- Username: `admin`
- Password: `Admin@123`

Change `ADMIN_USERNAME` and `ADMIN_PASSWORD` before deployment.

## Run
```bash
pip install -r requirements.txt
python app.py
```

Open:
- Farmer website: http://127.0.0.1:5000
- Admin login: http://127.0.0.1:5000/admin-login

The SQLite database `procurement.db` is created automatically.


## Multilingual implementation
- Framework: Flask + SQLite.
- Translation files are stored separately in `translations/en.json`, `kn.json`, `hi.json`, `mr.json`, `te.json`, and `ta.json`.
- The language selector is rendered from `base.html`, so it is available on every page.
- The selected language is stored in both the Flask session and a one-year `kisansetu_language` cookie.
- Server-rendered labels, headings, buttons, placeholders, flash messages, status/crop labels, and known notification messages use the active translation.
- The live queue API localizes crop/status labels for the currently selected language.
- Existing routes, database tables, CSS, and application features are preserved.


## Kisan Voice Connect (added)
- Logged-in farmers can record a voice message in their selected supported language, preview it, and submit it.
- Admins can open **Voice Messages** from the authenticated admin navigation, listen to farmer recordings, and record/send an audio reply.
- Farmers can listen to admin replies from **Voice Connect** while logged in.
- Audio is stored in `private_uploads/voice_messages/`, outside the public `static` directory.
- Audio playback is protected by login and farmer-ownership checks. Uploads are limited to 10 MB and supported audio extensions.
- Browser microphone access requires permission. Use `http://127.0.0.1:5000` for local development or HTTPS when deployed.
- Voice recordings and replies are audio-only; speech-to-text is not included.

## New farmer features added
- **Payment Tracker** (`/payments`): totals paid, pending/processing and all procurement records for the signed-in farmer.
- **Crop Prices & MSP** (`/crop-prices`): admin can add/update crop MSP and reference market prices per quintal; farmers can view published values. Verify all MSP values against the current official government notification before publishing.
- **Digital Farmer ID / QR** (`/farmer-id`): each farmer has a QR code and a simple Farmer ID card. The QR opens a minimal public card with farmer name, village, crop and ID only; it does not show phone or payment details.
- **Digital Procurement Receipt**: farmers can open each receipt from the dashboard or payment tracker and use **Print / Save as PDF**. A farmer can only access their own procurement receipts.
- A new `crop_prices` table is created automatically by `init_db()`. Existing tables and records are not dropped or reset.

## Dependency note
The QR image generator is included in `requirements.txt` (`qrcode[pil]`). After updating the project, run `pip install -r requirements.txt` again before starting the app.



## Recent updates
- Removed the QR code Farmer ID feature and its navigation link; the ordinary farmer ID card remains available internally.
- Added editable starter crop reference prices for 30 crops. The displayed Kishan Setu rate is calculated as market reference price minus ₹20 per quintal.
- Starter prices are examples only, not live market data. Admin should verify current mandi prices and official MSP notifications before real procurement.
- Language selector supports English, Kannada, Hindi, Marathi, Telugu, and Tamil. Template phrases that match translation dictionary entries are translated automatically; newly added/custom text should use the `t()` translation keys for complete coverage.


## Crop price negotiation update
- The published Kishan Setu starting rate is calculated as market reference price + ₹20 per quintal.
- Farmers can submit crop-price offers from **Price Bargaining** after login.
- Admins can accept, reject, or send a counter-offer with a message. Farmers can review the response in the same page.
- Starter crop prices are illustrative and editable by the admin; verify live mandi prices and official MSP notifications before real procurement.
- The QR Farmer ID feature has been removed from the dashboard and navigation.
