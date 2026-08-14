# Agent ↔ Unreal Protocol

Single WebSocket, `ws://127.0.0.1:8765`. Unreal is the client, Python is the server.

Two frame types on the same socket:
- **Binary frames** = raw PCM16, mono, 16 kHz, little-endian. Audio only.
- **Text frames** = JSON control messages.

Binary frames are always bracketed by an `audio_start` / `audio_end` pair carrying the same `seq`.

---

## Agent → Unreal

### `audio_start`
```json
{ "type": "audio_start", "seq": 41, "sample_rate": 16000, "channels": 1 }
```
Unreal opens a playback buffer and an Audio2Face stream for this `seq`. Binary frames that follow belong to it.

### `audio_end`
```json
{ "type": "audio_end", "seq": 41 }
```
No more audio for this utterance. Let the buffer drain naturally, then return to idle animation.

### `interrupt`
```json
{ "type": "interrupt", "seq": 41 }
```
**Flush the buffer immediately.** Stop playback within 100ms, kill the A2F stream, blend the face back to neutral over ~150ms. Sent when the customer starts talking over her.

### `state`
```json
{ "type": "state", "value": "listening" }
```
`value` ∈ `attract | waking | listening | thinking | speaking | presenting | farewell`.
Drives the body/idle animation layer only. Never gates audio.

### `ui`
```json
{ "type": "ui", "action": "show_product_frame", "payload": {
    "handle": "padron-1926-serie-no-2",
    "title": "Padrón 1926 Serie No. 2",
    "brand": "Padrón",
    "price": "$32.00",
    "image_url": "https://cdn.shopify.com/...",
    "size": "5½ × 52",
    "wrapper": "Maduro",
    "strength": "Full",
    "has_video": false,
    "has_model_3d": false,
    "attributes": { "Gas Mileage": "38 mpg", "Drivetrain": "AWD" }
}}
```
Spawn `BP_ProductFrame` at the right-hand socket, play the lift-and-present montage. If a frame is already up, cross-swap the texture instead of re-playing the montage.

`size`/`wrapper`/`strength` appear only when the catalog has them. `attributes` is an optional object of up to four label→value pairs from the store's own product sheet — render them as detail lines under the fixed fields, whatever the labels are. (The example above is a car store; a cigar store might send `{"Origin": "Nicaragua"}`.)

```json
{ "type": "ui", "action": "show_media", "payload": {
    "handle": "opus-x-humidor",
    "kind": "model_3d",
    "url": "file:///Content/Products/opus-x-humidor.glb"
}}
```
`kind` ∈ `images | video | model_3d`.
- `model_3d` → load GLB, attach to hands, enable turntable + touch-drag rotate.
- `video` → full-screen player, black letterbox (never grey).
- `images` → swipeable gallery.

```json
{ "type": "ui", "action": "open_menu", "payload": { "menu_id": "humidors" } }
{ "type": "ui", "action": "close_overlay", "payload": {} }
```

```json
{ "type": "ui", "action": "show_web_page", "payload": {
    "id": "signup",
    "url": "https://cigarinc.com/pages/sign-up",
    "title": "Join the Cigar Inc. list"
}}
```
Open the URL in a **picture-in-picture web widget** (UE Web Browser widget), floating over the scene — never full-screen, never replacing her. It must have a **visible ✕ close button**. She stays rendered and keeps talking behind/beside it; the conversation never pauses. When the customer taps ✕, Unreal removes the widget and sends `{ "type": "touch", "target": "close", "id": "signup" }` so she picks the conversation back up. This is the pattern for every overlay: frame with an ✕, close returns to her, talk continues.

### `error`
```json
{ "type": "error", "code": "shopify_unreachable", "message": "..." }
```
Log it. Do not surface to the customer — she handles it verbally.

---

## Unreal → Agent

### `hello`
```json
{ "type": "hello", "client": "ue5", "version": "1.0" }
```
First message after connect. Agent replies with a `state` message.

### `touch`
```json
{ "type": "touch", "target": "menu",          "id": "humidors" }
{ "type": "touch", "target": "product_frame", "handle": "padron-1926-serie-no-2" }
{ "type": "touch", "target": "product_grid",  "handle": "opus-x-humidor" }
{ "type": "touch", "target": "close" }
{ "type": "touch", "target": "close",         "id": "signup" }
{ "type": "touch", "target": "mic" }
```
Every touch is injected into the conversation as context so **she reacts out loud.** Tapping a humidor in a menu should make her say something about that humidor — not silently open a page.

### `wake` / `sleep`
```json
{ "type": "wake", "trigger": "proximity" }
{ "type": "sleep", "reason": "timeout" }
```
`trigger` ∈ `proximity | touch | wake_word`.

### `speech_detected`
```json
{ "type": "speech_detected" }
```
Local VAD in Unreal fired. Agent uses this to send `interrupt` fast, without waiting for a Deepgram transcript. **This is what makes barge-in feel instant.**

---

## Rules

1. Binary audio frames outside an `audio_start`/`audio_end` window are dropped.
2. Unreal never decides what to show. It renders what it's told.
3. The agent never assumes Unreal is connected. If the socket is down it keeps running and buffers nothing.
4. `seq` increments per utterance and never resets within a session. Late frames from a superseded `seq` are dropped.
