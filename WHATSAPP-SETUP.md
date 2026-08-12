# WhatsApp Cloud API — Setup Guide

How to wire a Meta WhatsApp Business test app to the `comm.gateway` adapter.

---

## 1. Prerequisites

- A Meta developer account with an app (App Dashboard → `developers.facebook.com`)
- WhatsApp product added to the app (App Dashboard → WhatsApp → Getting Started)
- A **test phone number** provisioned by Meta (or your own number)
- A **System User** with `whatsapp_business_messaging` + `whatsapp_business_management` scopes

---

## 2. Required credentials

| Value | Where to find |
|---|---|
| **Access Token** | App Dashboard → System Users → Generate New Token (scopes: `whatsapp_business_messaging`, `whatsapp_business_management`). Use a **permanent** token. |
| **Phone Number ID** | App Dashboard → WhatsApp → Getting Started → your test number → Phone Number ID. |
| **App Secret** | App Dashboard → App Settings → Basic → App Secret (used to HMAC-verify webhooks). |
| **Verify Token** | Any string you choose (≥8 chars); set it in the Meta Dashboard → WhatsApp → Configuration → Webhook → Verify Token. |

---

## 3. VOX configuration

Add these keys to the agent's `secrets.vault` or `.env`:

```bash
WHATSAPP_ACCESS_TOKEN=EAAG...          # System User permanent token
WHATSAPP_PHONE_NUMBER_ID=123456789012345
WHATSAPP_APP_SECRET=your-app-secret
WHATSAPP_VERIFY_TOKEN=your-verify-token
WHATSAPP_API_VERSION=v25.0             # current stable; override if Meta rotates
GATEWAY_PORT=8001
```

All three secrets (`ACCESS_TOKEN`, `APP_SECRET`, `VERIFY_TOKEN`) are automatically
injected from the agent's vault via `SENSITIVE_PARAMS`.

---

## 4. Public HTTPS URL

Meta requires a **publicly reachable HTTPS** endpoint for the webhook callback.

### Local development (tunnel)

```bash
# ngrok
ngrok http 8001
# → https://<random>.ngrok-free.app

# cloudflared
cloudflared tunnel --url http://localhost:8001
# → https://<random>.trycloudflare.com
```

Use the HTTPS URL as the **Callback URL** in the Meta Dashboard.

---

## 5. Meta Dashboard — Webhook configuration

1. App Dashboard → WhatsApp → Configuration → Webhook
2. **Callback URL**: `https://<your-public-url>/webhook/whatsapp`
3. **Verify Token**: the string you set in `WHATSAPP_VERIFY_TOKEN`
4. Save — Meta sends a GET handshake; the adapter echoes `hub.challenge` → webhook is verified
5. Subscribe to the **messages** field (for inbound messages and status updates)

---

## 6. Smoke test

### Send a text to your test number

```bash
curl -s -X POST "https://graph.facebook.com/v25.0/${WHATSAPP_PHONE_NUMBER_ID}/messages" \
  -H "Authorization: Bearer ${WHATSAPP_ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "messaging_product": "whatsapp",
    "recipient_type": "individual",
    "to": "<your-phone-with-country-code>",
    "type": "text",
    "text": {"body": "Hello from VOX"}
  }'
```

### Verify inbound

Send a WhatsApp message to the test number from your phone.
The VOX log should show:

```
[comm.gateway] Gateway server listening on 0.0.0.0:8001
```

and the message should appear in the agent's `inbound_message` handler (the agent's `chat.py` role module).

---

## 7. Notes

- **HTTPS required** — Meta rejects plain HTTP callback URLs. Self-signed certificates are not supported.
- **24-hour window** — after a customer messages you, you have 24 hours to reply (using free-form messages). Outside that window, only approved template messages work.
- **Test numbers** are limited to a small number of contacts; upgrade to production for real traffic.
- **mTLS** is optional and not enabled by default. If you enable it, you'll need to trust the Meta outbound API CA certificate.
- **Status updates** (sent/delivered/read) arrive as webhook events; the adapter surfaces them as `content_type="event"` so agents can track delivery if needed.
- **Media** (image/audio/document/video) — the adapter parses caption and type; fetching the actual media bytes requires a second Graph API call (not yet implemented; the media `id` is available in `raw_payload`).
