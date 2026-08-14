# START HERE

Do these in order. Nothing is optional and nothing is out of sequence.
**YOU** = you do it. **CC** = Claude Code does it.

---

## Phase 0 — Set up the room (before any code)

**1. YOU** — Move the Windows PC to the lobby. Sit it behind or under the Proto Luma.

**2. YOU** — Cable it up:
- HDMI 2.1 from the PC to the Luma
- USB from the Luma's touch panel back to the PC
- USB beamforming mic array on the counter, near the glass
- Ethernet to the PC

**3. YOU** — In Windows Display Settings: set resolution **2160 × 3840**, orientation **portrait**. Open Paint and drag a line across the Luma — if the line follows your finger, touch works.

**4. YOU** — Give the PC a **static IP**, enable **Remote Desktop**, and connect to it from your Mac Mini. **Do not go further until this works reliably.** Every step after this you'll be doing from your room.

**5. YOU** — On the PC, install:
- NVIDIA driver 551.78 or later
- Visual Studio 2022, with the **Desktop development with C++** workload
- Epic Games Launcher, then Unreal Engine 5.6
- Python 3.11
- Git
- Node.js, then Claude Code

**6. YOU** — Get four API keys and keep them somewhere handy:
- Anthropic (console.anthropic.com)
- ElevenLabs
- Deepgram
- Shopify Storefront access token

---

## Phase 1 — Her brain (no voice, no face)

**7. YOU** — Unzip `cigar-avatar` onto the PC. Copy `.env.example` to `.env` and paste in the four keys.

**8. YOU** — Put your product Excel at `data/products.xlsx`.

**9. YOU** — Open Claude Code in the `cigar-avatar` folder and paste the prompt from `PROMPT.txt`.

**10. CC** — Builds the catalog. It will tell you if columns are missing.

**11. YOU** — Fix any gaps it reports in the Excel. Especially the `time_of_day`, `blood_type`, `palate_profile` columns — those are what make her recommendations yours and not generic.

**12. CC** — Gets the text brain running: `python -m agent.brain --cli`

**13. YOU** — **Talk to her in the terminal for a full day.** Pretend to be five different customers. Take notes on where she's pushy, where she rambles, where she misses.

**14. CC** — Revises `agent/persona.md` from your notes. Repeat 13–14 until she sells the way you'd sell.

> This phase is the project. Everything after it is craft. Do not rush it.

---

## Phase 2 — Her voice

**15. YOU** — Pick her voice in ElevenLabs. Either a stock voice, or clone someone (needs their written consent). Put the voice ID in `.env`.

**16. CC** — Wires Deepgram + ElevenLabs. Measures latency, tunes it under 1.2 seconds.

**17. YOU** — Talk to her out loud through the PC. Approve the voice, or pick a different one and repeat.

---

## Phase 3 — Her body

**18. YOU** — Build her in **MetaHuman Creator** (free, in your browser, Epic account). Blend the face, pick body type, hair, skin, wardrobe. **This is where "creating the avatar" actually happens.** Takes an afternoon.

**19. CC** — Creates the Unreal project, portrait 2160×3840, imports your MetaHuman, sets up the black void and three-point lighting with rim lights, adds an idle animation loop, packages it.

**20. YOU** — **Look at her on the Luma.** Judge the rim lighting in the box. Lighting approved on a monitor will be wrong. Give notes, CC adjusts, repeat until she reads as solid and present.

---

## Phase 4 — Her face moves

**21. CC** — Installs the NVIDIA ACE + Audio2Face-3D plugins, sets **local inference**, maps to the MetaHuman rig, tests with a WAV file.

**22. YOU** — Watch her lip-sync in the Luma. If the mouth looks rubbery or lags, say so now — it gets harder to fix later.

---

## Phase 5 — Connect brain to body

**23. CC** — Writes the `AvatarBridge` C++ plugin exactly per `docs/PROTOCOL.md`. WebSocket, PCM playback, Audio2Face feed, Blueprint events, barge-in flush.

**24. YOU** — **First real test.** Stand in front of the Luma and talk to her. She should answer within about a second, and stop instantly when you cut her off.

---

## Phase 6 — The selling tools

**25. CC** — Builds `BP_ProductFrame`: spawns in her hand, lift-and-present animation, streams the product image, tap to expand, X to dismiss.

**26. CC** — Builds the floating telescoping menus.

**27. YOU** — Deliver the **GLB 3D models** for humidors, Corona humidors, Opus X humidors, Opus X cases. Needed by this step. If you don't have them, commission them now — a 3D artist needs 1–2 weeks.

**28. CC** — Builds the runtime GLB loader so she can hold and turn them.

**29. CC** — Builds the attract/wake state machine, the 21+ gate, and the compliance filter.

---

## Phase 7 — Ship

**30. CC** — Packages to a Windows `.exe`, adds auto-launch on boot, crash watchdog, and a 4am nightly reboot.

**31. YOU** — Run it live in the lobby for a week before you tell anyone. Watch real customers. The first ten conversations will teach you more than all of Phase 1.

---

## What you need to have ready, and when

| Needed by | Thing |
|---|---|
| Step 8 | Product Excel |
| Step 8 | Four API keys |
| Step 15 | Voice decision (and consent, if cloning) |
| Step 18 | Her face, body, wardrobe |
| Step 27 | GLB 3D models — **order these in Phase 1** |
