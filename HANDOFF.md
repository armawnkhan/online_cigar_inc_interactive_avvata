# Handoff — state of the project as of 2026-08-13

## PIVOT (2026-08-13, per Arman): this repo is now the ONLINE project

Arman split the work in two. The UE5/MetaHuman local-render build continues
in the old repo (Cigar-Inc-Interactive-Avator). THIS repo —
Online_Cigar-Inc_Interactive_Avatar — is a pure web project: one hosted web
app, opened on the Proto Luma's browser.

- Avatar: **Anam.ai** (real-time avatar over WebRTC; does STT + TTS +
  lip-sync itself). Anam's own LLM is disabled via
  `llmId: "CUSTOMER_CLIENT_V1"`; **Claude stays the only brain**, unchanged.
- New entry point: `uvicorn agent.web:app` (agent/web.py). Front end in
  `web/`. Architecture + WS protocol: **docs/WEB.md** (README.md has the
  quick start). docs/PROTOCOL.md and agent/server.py + voice.py are legacy
  (UE5 path) — kept, not deleted.
- Deepgram/ElevenLabs are NOT needed on this path; keys stay in .env.example
  under a "legacy" heading.
- Built and browser-tested in dev mode (no keys): attract → wake → menus →
  product grid → product frame → QR page hand-off → 3D humidor viewer all
  verified on 2026-08-13.
- **Waiting on Arman**: ANAM_API_KEY (he creates the account, arriving
  tomorrow ≈2026-08-14), plus ANTHROPIC_API_KEY in .env to light up the
  conversation. No code changes needed when they land.
- Note: cigarinc.com (Shopify) sends `frame-ancestors 'none'` — pages CANNOT
  be iframed. The PiP overlay therefore shows a scannable QR code for
  external pages (sign-up, product pages); customers finish on their phone.
  If Arman wants true on-kiosk sign-up later, build a native form that posts
  to the list provider's API instead.

Everything below is the pre-pivot record, still accurate for the engine.

---

Read BRIEF.md, docs/PROTOCOL.md and START-HERE.md first. They are the spec.
This file records where the build left off on Arman's Mac, so work can
continue on another machine.

## Done

- **Repo + hygiene.** .gitignore covers Unreal build dirs, `.env`,
  `__pycache__/`, `.venv/`, `data/catalog.db`, `data/*.xlsx`. `.env` was
  verified ignored before the first commit.
- **Environment (Mac).** Python 3.11 venv at `.venv/` created with `uv`;
  all of requirements.txt installed. On a new machine: install Python 3.11,
  `python -m venv .venv`, `pip install -r requirements.txt`.
- **Step 1 of BRIEF §5 — Catalog: COMPLETE and verified.**
  `scripts/build_catalog.py data/products.xlsx` builds `data/catalog.db`,
  and `python -m agent.catalog search "maduro medium"` returns good results.

## Interim product data (important)

Arman has not supplied the curated product Excel yet. The current
`data/products.xlsx` was generated from the **live Cigar Inc. Shopify store**
(50 real products of 1,013 active) — real handles, titles, brands, prices,
descriptions, images. Nothing was invented.

- `data/*.xlsx` and `data/catalog.db` are git-ignored, so they are **not in
  this repo**. Copy them from the Mac, or regenerate from Shopify.
- Columns that are NULL in the interim data: wrapper, origin, strength,
  tasting_notes, video_url, model_3d_url, and all five recommendation
  columns (time_of_day, blood_type, palate_profile, occasion, pairs_with).
  Those come from Arman's real Excel later; the build script warns about
  them loudly on every run — that is expected for now.
- When the real Excel arrives: overwrite `data/products.xlsx`, re-run
  `python scripts/build_catalog.py data/products.xlsx`.

## Email sign-up feature (added 2026-08-13, per Arman)

Hannah offers the Cigar Inc. email list once per conversation, at a natural
moment: fifteen percent off the first order, first access to new releases,
early and subscriber-only deals. When the customer says yes she calls the
`open_signup` tool, which emits `ui / show_web_page` with
https://cigarinc.com/pages/sign-up.

- Agent side is DONE: tool in `agent/tools.py`, offer behavior in
  `agent/persona.md` ("The subscriber list"), close reaction in `agent/server.py`.
- The deal TERMS live only in `data/house_rules.md` under "The subscriber
  offer" — Arman edits that file to change the deal; persona never hardcodes
  it. Empty section = she stops offering the list.
- Unreal side is spec'd in docs/PROTOCOL.md: `show_web_page` opens the URL in
  a **picture-in-picture Web Browser widget with a visible ✕** — never
  full-screen, she keeps rendering and talking. ✕ removes the widget and sends
  `touch target:"close" id:"signup"`; she then resumes the conversation.
  This frame-with-✕ / close-returns-to-her pattern applies to every overlay.

## Multi-business restructure (2026-08-13, per Arman)

The engine is now generic; ONE business is defined entirely by files in
`data/` — see docs/CONFIG.md. Key changes:

- `agent/persona.md` is GONE. Generic behavior moved to `agent/engine.md`
  (code-owned); the business persona — including her NAME — is now
  `data/persona.md`. Store facts are `data/store_facts.md`. Rules stay in
  `data/house_rules.md`, which now also carries the sign-up URL (parsed at
  startup; no URL = open_signup disabled and she never offers the list).
- System prompt = engine + persona + facts + rules, assembled in
  `agent/brain.py::load_system_text()`. Anywhere BRIEF/START-HERE says
  "agent/persona.md", read "data/persona.md + agent/engine.md".
- Commerce platforms are pluggable: any source that can produce
  `data/products.xlsx` works (columns in docs/CONFIG.md). The Shopify live
  price/stock overlay is just the built-in connector, off unless its env
  vars are set — without it prices flag stale and she hedges safely.

## Next: Step 2 of BRIEF §5 — the text brain

1. `cp .env.example .env` and fill in `ANTHROPIC_API_KEY` (other keys can
   wait until step 3). Confirm `.env` is git-ignored before committing
   anything.
2. Run `python -m agent.brain --cli` and get a full selling conversation
   working in the terminal. Read agent/brain.py, tools.py, catalog.py,
   persona.md before changing anything — the code is written; complete it,
   don't rewrite it.
3. Stop after step 2 and show Arman the result. He will play five different
   customers and give notes on persona.md (START-HERE steps 13–14).

## Standing rules (from Arman — apply to every step)

- Never invent a product, price, spec, or company fact.
- Anthropic prompt caching ON for system prompt + tool schemas.
- Audio2Face-3D is LOCAL inference only. Never a remote A2F server.
- Audio is PCM16 mono 16 kHz end to end.
- Implement docs/PROTOCOL.md exactly.
- Claude never describes the UI; tool calls drive the scene.
- Background pure #000000; rim lights behind-left and behind-right.
- Tobacco compliance in the persona prompt AND a code-level output filter.
- Stop after each numbered BRIEF §5 step and show Arman before continuing.
- Always ask before pushing to remote.
