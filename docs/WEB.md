# Web kiosk architecture (the online path)

This is the current build target: one web app, hosted anywhere, opened
full-screen on the Proto Luma's browser. It replaces the UE5/MetaHuman
local-render path (docs/PROTOCOL.md, now legacy).

```
Proto Luma browser (web/)                         Host (agent/web.py)
┌─────────────────────────────┐                  ┌──────────────────────────┐
│ Anam SDK                    │  WebRTC          │ FastAPI                  │
│  avatar video + mic + STT   │◀────────────────▶│  /api/anam-session-token │──▶ api.anam.ai
│  TTS + lip sync             │                  │  /api/menus /products    │
│                             │   /ws JSON       │  /ws  ── WebSession      │
│ kiosk.js  menus, frame,     │◀────────────────▶│        └─ Brain (Claude) │──▶ api.anthropic.com
│  media, PiP, touch          │                  │            └─ catalog.db │
└─────────────────────────────┘                  └──────────────────────────┘
```

Anam's own LLM is disabled (`llmId: "CUSTOMER_CLIENT_V1"`); Anam does the
face, the ears and the voice. Claude — agent/brain.py, unchanged from the
UE5 path — remains the only brain, with the same tools, persona files and
catalog. Deepgram and ElevenLabs are NOT used on this path.

## The /ws protocol

Text JSON frames both ways. Direct adaptation of docs/PROTOCOL.md with the
audio plumbing removed (Anam carries audio over its own WebRTC connection).

### Client → server

```json
{ "type": "hello", "client": "web", "version": "1.0" }
{ "type": "wake",  "trigger": "touch" }
{ "type": "sleep", "reason": "manual" }
{ "type": "user_text", "text": "what do you have for a beginner?" }   // Anam transcript or typed
{ "type": "interrupted" }                                             // customer talked over her
{ "type": "touch", "target": "menu",          "id": "Cigar" }
{ "type": "touch", "target": "product_grid",  "handle": "..." }
{ "type": "touch", "target": "product_frame", "handle": "..." }
{ "type": "touch", "target": "close", "id": "signup" }
{ "type": "touch", "target": "mic" }
```

Every touch is injected into the conversation so she reacts out loud —
a silent page-open is the failure mode we're avoiding.

### Server → client

```json
{ "type": "state", "value": "listening" }        // attract|waking|listening|thinking|speaking|presenting|farewell
{ "type": "speak", "text": "One sentence." }     // -> anam talk stream chunk (or dev TTS)
{ "type": "speak_end" }                          // -> talkStream.endMessage()
{ "type": "ui", "action": "show_product_frame", "payload": { ... } }   // same payloads as PROTOCOL.md
{ "type": "ui", "action": "show_media",   "payload": { "kind": "video|images|model_3d", "url": "..." } }
{ "type": "ui", "action": "open_menu",    "payload": { "menu_id": "..." } }
{ "type": "ui", "action": "show_web_page","payload": { "id": "signup", "url": "...", "title": "..." } }
{ "type": "ui", "action": "close_overlay","payload": {} }
{ "type": "error", "code": "brain_offline", "message": "..." }
```

`speak` fires per sentence as Claude streams, so the avatar starts talking
before the full response exists — that is the latency budget.

## Sentence → lips flow

1. Brain flushes a sentence → server sends `speak`.
2. kiosk.js opens an Anam `TalkMessageStream` (one per response) and calls
   `streamMessageChunk(text, false)`.
3. On `speak_end` → `endMessage()`. Anam speaks and lip-syncs.
4. If the customer talks over her, Anam fires `TALK_STREAM_INTERRUPTED`;
   kiosk.js drops the stream and tells the server `interrupted`.

## Dev mode (no ANAM_API_KEY)

`/api/config` reports `anam_enabled: false`; the kiosk shows a placeholder
avatar, uses browser SpeechRecognition for input, speechSynthesis for
output, and shows a typed-input bar. The whole protocol, menu system and
overlay stack run for real. Set the key, reload, and the real avatar
appears — no code change.

## Deploying to the Luma

Any host that can run Python works (Render, Railway, Fly, a VPS):

```
pip install -r requirements.txt
python scripts/build_catalog.py data/products.xlsx
uvicorn agent.web:app --host 0.0.0.0 --port 8000
```

Serve behind HTTPS (Anam's mic capture requires a secure origin), open
`https://<host>/` in the Luma's browser, full-screen/kiosk mode.
Multi-touch works out of the box — it's a web page.

## Hosting under a subpath

To serve at `https://example.com/hannah/` instead of the domain root, set one
env var:

```
APP_BASE_PATH=/hannah
```

The whole app moves under that prefix — page, `/api`, `/ws`, `/static`. The
front end works out the prefix from its own module URL (`kiosk.js` is always at
`<root>/static/kiosk.js`), so nothing else needs configuring, and `/hannah`
without the trailing slash redirects to `/hannah/`.

Because the app owns the prefix, the reverse proxy must forward the path
**untouched** — do not strip it. nginx:

```nginx
location /hannah/ {
    proxy_pass         http://127.0.0.1:8000;   # no trailing slash = no rewrite
    proxy_http_version 1.1;
    proxy_set_header   Upgrade $http_upgrade;   # required: /ws is a WebSocket
    proxy_set_header   Connection "upgrade";
    proxy_set_header   Host $host;
    proxy_set_header   X-Forwarded-Proto $scheme;
}
```

The two `Upgrade`/`Connection` headers are not optional. Without them the
avatar loads and then never hears anything — the WebSocket carrying the whole
conversation silently fails to connect.

This is a hosted Python service, not a folder of files: it needs Python 3.11,
a long-running `uvicorn` process, HTTPS, and WebSocket proxying. Uploading a
zip to typical shared hosting will not work.
