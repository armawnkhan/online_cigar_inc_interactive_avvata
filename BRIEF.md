# Cigar Inc. — Holographic Avatar Concierge

**Target device: Proto Luma.** 86" multi-touch holographic display, 4K, portrait 9:16.

Paste this whole file into Claude Code as the project brief. The `agent/` and `scripts/` code in this repo is written and working-shaped — your job is to complete it, wire it up, and build the Unreal side.

---

## 0. What this is

A life-size female cigar concierge, standing full body inside the Luma. She greets customers, has a real conversation, and recommends cigars. When she recommends something, **she picks up a framed product card and holds it out** — a real 3D object in her hands, not a video overlay. Tap it and it opens full-screen. Humidors and Opus X pieces load as actual 3D models she can turn in her hands.

Voice-first. Touch is the fallback, not the primary interface.

---

## 1. Architecture — read this before writing anything

Everything runs **on one Windows box behind the Luma**. Nothing about the visual layer touches the cloud.

```
┌─────────────────────── Windows PC (RTX 5090) ───────────────────────┐
│                                                                     │
│   agent/  (Python)                    ue5/  (Unreal Engine 5)       │
│   ┌──────────────────┐                ┌───────────────────────┐     │
│   │ Deepgram STT     │                │ MetaHuman (full body) │     │
│   │        ↓         │   WebSocket    │ Audio2Face-3D (local) │     │
│   │ Claude + tools   │ ←───────────→  │ Product frame actor   │     │
│   │        ↓         │  localhost     │ 3D product models     │     │
│   │ ElevenLabs TTS   │  :8765         │ Floating menu panels  │     │
│   │        ↓         │                │ Pure black void       │     │
│   │ PCM audio out    │                └───────────┬───────────┘     │
│   └──────────────────┘                            │ HDMI 2160×3840  │
│           ↑                                       ▼                 │
│      USB mic array                          ┌──────────┐            │
│                                             │ Proto    │            │
│      USB touch ← ─────────────────────────  │ Luma     │            │
└─────────────────────────────────────────────┴──────────┴────────────┘
```

**Two processes, one WebSocket between them.** The Python agent is the brain and the voice. Unreal is the body and the world. Neither knows how the other works internally.

### The rule that makes this work

**Claude does not describe the scene. Claude calls tools, and tool calls become scene commands.**

When she recommends a Padrón, Claude calls `show_product_frame("padron-1926-no-2")`. The agent forwards it to Unreal as a UI command. Unreal spawns the frame actor and plays the "lift and present" animation. She speaks and reaches out at the same time.

Never try to parse UI intent out of her speech. It will fail in front of a customer.

---

## 2. Hardware

| | |
|---|---|
| GPU | RTX 5090 (4090 acceptable). Audio2Face-3D local inference + MetaHuman at 4K needs headroom. |
| CPU / RAM | Anything modern, 32GB |
| Output | HDMI 2.1 → Luma, **2160×3840 @ 60Hz portrait** |
| Mic | Beamforming USB array (Shure MXA / Rode NT-USB Mini class). Customer stands 3–6 ft back — a normal mic will not work. |
| Audio out | Luma has built-in speakers over HDMI. Use them; sound must come from where she is. |
| Touch | Luma multi-touch over USB, appears to Windows as HID touch. Unreal reads it natively. |
| Network | Ethernet. Only the agent needs it. If it drops, she still renders — she just apologizes. |

---

## 3. Proto Luma content rules (non-negotiable — the illusion dies without these)

1. **Background is pure `#000000`.** Not near-black. Render nothing behind her. No fog, no gradient, no vignette, no bloom spill into empty space. Set the Unreal background to black and disable any post-process that lifts blacks.
2. **Rim light is mandatory.** Against a black void, a front-lit person reads as a floating head and hands. Use a strong warm key from front-left, plus **two cool rim lights from behind-left and behind-right** to trace her silhouette. That edge separation is what sells the volume.
3. **Safe areas.** Keep everything out of the bottom 10% and top 5%. Her feet should land around 12% up from the bottom edge.
4. **She must never leave frame.** Constrain the camera and her locomotion. A holobox with an empty box is a broken holobox.
5. **Emissive UI.** Menus and frames should be self-lit gold on black, no panel background fills. A grey panel becomes a visible grey rectangle in the glass.
6. **Test at 3ft, 6ft, and 10ft.** Type that reads on a monitor disappears in a holobox. Minimum body text ~48px at 2160 wide.

---

## 4. Repo layout

```
cigar-avatar/
├── BRIEF.md                  ← this file
├── .env.example
├── requirements.txt
├── docs/
│   └── PROTOCOL.md           ← agent ↔ Unreal WebSocket contract
├── scripts/
│   └── build_catalog.py      ← Excel → SQLite + FTS5
├── agent/
│   ├── server.py             ← WebSocket server + session state machine
│   ├── brain.py              ← Claude streaming + tool use
│   ├── tools.py              ← tool schemas + implementations
│   ├── voice.py              ← Deepgram STT + ElevenLabs TTS
│   ├── catalog.py            ← SQLite queries + Shopify overlay
│   └── persona.md            ← her system prompt
├── data/
│   ├── products.xlsx         ← operator supplies
│   └── catalog.db            ← generated
└── ue5/
    └── CigarConcierge/       ← Unreal project (you build this)
```

---

## 5. Build order

Do not skip ahead. Each step is testable on its own.

**1 — Catalog.** Run `scripts/build_catalog.py` against the real Excel. Verify with `python -m agent.catalog search "maduro medium"`. If the recommendation columns aren't there, stop and ask the operator.

**2 — Text brain.** `python -m agent.brain --cli`. Have a full selling conversation in the terminal. No voice, no avatar. **This is the hard part.** A gorgeous hologram with a mediocre conversation is worthless. Spend real time on `persona.md` here.

**3 — Voice loop.** Add Deepgram + ElevenLabs. Talk to her through the laptop. Measure latency (§7). Tune until it's under budget. Still no Unreal.

**4 — Unreal scene, static.** New UE5 project, portrait 2160×3840. Import a MetaHuman. Black void, three-point lighting per §3. Idle animation loop. Package it and put it on the Luma. **Look at it in the box before going further.** Lighting decisions made on a monitor will be wrong.

**5 — Audio2Face-3D.** Install the ACE Unreal plugin + Audio2Face-3D Models plugin. Use the included MetaHuman sample mapping. Feed it a WAV file. Confirm lip-sync locally, not remotely.

**6 — The bridge.** Build the `AvatarBridge` plugin (§6). Agent streams PCM to Unreal, Unreal plays it and drives A2F. This is the integration that either works or doesn't — budget time.

**7 — Product frame.** `BP_ProductFrame` actor, spawns in her right hand socket, plays a lift-and-present montage. Texture streams the product image from URL. Tap → full-screen detail. X → dismiss, she returns to idle.

**8 — Menus.** Floating emissive panels in world space, telescoping. Selecting a product routes through `get_product` so **she comments on it out loud.** The menus and the conversation are one system.

**9 — 3D models.** glTF runtime loader for GLB product models. Humidors, Corona humidors, Opus X humidors, Opus X cases. She holds them, rotates them, opens the lid.

**10 — Attract/wake state machine + hardening.** §8.

---

## 6. The Unreal bridge plugin

Write a C++ plugin `AvatarBridge` exposing Blueprint nodes. It owns one WebSocket client to `ws://127.0.0.1:8765`.

**Responsibilities:**
- Connect, auto-reconnect with backoff, expose `OnConnectionChanged`.
- Receive binary PCM16 @ 16kHz mono → push into a `USoundWaveProcedural` for playback **and** into the Audio2Face-3D stream node. Both from the same buffer, same clock. If they diverge you get lip-sync drift.
- Receive JSON control messages → broadcast typed Blueprint events (`OnShowProductFrame`, `OnShowMedia`, `OnOpenMenu`, `OnCloseOverlay`, `OnStateChanged`, `OnInterrupt`).
- `OnInterrupt` must **flush the audio buffer immediately.** Barge-in is the single biggest realism factor. If she keeps talking over the customer, the illusion is gone.
- Send JSON up: touch events, wake/sleep, mic toggle.

Protocol is in `docs/PROTOCOL.md`. Implement it exactly.

**Latency note:** run Audio2Face-3D inference **locally**, not against a remote NIM. A known result from people doing this is ~367ms of audio delay needed to match remote-inference lip-sync — unacceptable here. Local inference on the same GPU removes it.

---

## 7. Latency budget

| Stage | Target |
|---|---|
| Endpointing (customer stops talking) | 300–500ms |
| Deepgram final transcript | ~100ms |
| Claude first token | 400–700ms |
| ElevenLabs first audio byte | ~100ms |
| Bridge + A2F + render | ~50ms |
| **First sound out of the Luma** | **≤ 1.2s** |

Required:
- **Stream Claude tokens into TTS as they arrive.** Never wait for the full response. Split on sentence boundaries and pipe.
- **Filler on slow tools.** If a tool will take >500ms, emit a short natural line ("Let me see what we have…") while it runs. `agent/brain.py` has a hook for this.
- **Barge-in cuts audio in <100ms.**
- Keep the Deepgram and ElevenLabs sockets open for the whole session. Reconnecting mid-conversation costs a full second.

---

## 8. Session state machine

| State | Behavior |
|---|---|
| `attract` | She's present and idle-animating — breathing, weight shifts, occasional glance around. **No STT, no LLM, no TTS running.** Costs nothing. Soft prompt: "Say hello, or tap to begin." |
| `waking` | Proximity sensor or touch or wake-word fires. She turns toward the customer, makes eye contact. Sockets open. ~800ms of animation covers the connection time. |
| `listening` | Mic hot, subtle attentive lean, blink rate up. |
| `thinking` | Only if >600ms. Small head tilt, eyes up-left. Never a spinner. |
| `speaking` | A2F driving the face, gesture layer blending on top. |
| `presenting` | Frame or model in hands. |
| `farewell` | 45s of silence → "I'll be right here." → back to `attract`, sockets close. |

Cap sessions at 10 minutes. Nightly reboot at 4am.

**Costs at ~60 conversations/day × 3 min:** Claude ~$60/mo, ElevenLabs ~$99/mo, Deepgram ~$30/mo. **No avatar streaming bill — that line item is zero.** Electricity is your GPU cost.

---

## 9. Data

**Excel → SQLite.** `scripts/build_catalog.py`. Expected columns (normalize headers, log loudly on anything unmapped — never silently drop):

`handle` (Shopify handle, the join key), `title`, `brand`, `category`, `price`, `size`, `ring_gauge`, `length`, `shape`, `wrapper`, `origin`, `strength`, `tasting_notes`, `description`, `image_url`, `video_url`, `model_3d_url`, and the recommendation columns `time_of_day`, `blood_type`, `palate_profile`, `occasion`, `pairs_with`.

FTS5 index over `title, brand, tasting_notes, description, wrapper, origin`.

**Shopify overlay.** `get_product` hits the Storefront API by handle for live price and stock, cached 5 min. If Shopify is unreachable, fall back to the Excel price and set `price_stale: true` — the persona then says "let me confirm the current price at the register" instead of quoting a possibly-wrong number.

**Never fabricate a field.** Missing column → NULL → tool omits it.

---

## 10. Compliance

Tobacco retail, public-facing, life-size. Build these in:

- **21+ gate on the attract screen** before any session starts.
- **No health claims, ever.** Enforce in the persona prompt *and* as a post-generation string filter in `brain.py`. Do not rely on the prompt alone.
- Never imply tobacco is safe or beneficial.
- Required tobacco warning text on the frame, permanently visible.
- Small "AI concierge" label — she must not be presented as a real person.
- If someone appears to be a minor or states they're under 21, she politely declines and offers to help with something else.

---

## 11. Ask the operator before starting

- The product Excel (blocks step 1)
- Shopify Storefront API token
- Her name
- Voice: clone a specific person (needs their recorded consent) or an ElevenLabs stock voice
- MetaHuman: build in MetaHuman Creator, or scan/model a specific person (again — consent)
- Wardrobe direction — she should look like she works at Cigar Inc.
- Which products already have GLB models and video
