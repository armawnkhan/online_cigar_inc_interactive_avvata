# Online Cigar Inc. Interactive Avatar

A web-based, server-hosted AI salesperson kiosk for Cigar Inc., built for
the Proto Luma (86" 4K, 9:16 portrait, multi-touch). One web app: an
[Anam](https://anam.ai) real-time avatar is the face and voice, Claude is
the brain, the store's own Excel catalog is the knowledge.

This repo is the **online** successor to the UE5/MetaHuman project
(Cigar-Inc-Interactive-Avator). The brain, catalog, persona files and tool
set carried over unchanged; the renderer moved from Unreal to the browser.

## Layout

```
agent/          the engine (business-agnostic)
  web.py        ← FastAPI kiosk server: static UI, Anam tokens, /ws     [RUN THIS]
  brain.py      Claude, streaming, tool use, sentence-level flushing
  tools.py      data tools (catalog) + UI tools (frame, menu, media, signup)
  catalog.py    SQLite + FTS over the Excel; optional live Shopify overlay
  engine.md     generic selling behavior (code-owned)
  server.py     legacy UE5 WebSocket server  (kept for reference)
  voice.py      legacy Deepgram/ElevenLabs pipeline (not used by web path)
web/            the kiosk front end (index.html, kiosk.css, kiosk.js)
data/           THE BUSINESS: persona.md, store_facts.md, house_rules.md,
                products.xlsx → catalog.db, models/*.glb
docs/WEB.md     ← current architecture + /ws protocol
docs/PROTOCOL.md  legacy UE5 protocol
scripts/build_catalog.py   Excel → catalog.db
```

## Run it

```
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # then fill in keys
python scripts/build_catalog.py data/products.xlsx
uvicorn agent.web:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 — portrait window for the true kiosk feel.

Keys (in `.env`):

| Key | Needed for | Without it |
|---|---|---|
| `ANTHROPIC_API_KEY` | the conversation | menus/product browsing still work; she can't talk |
| `ANAM_API_KEY` | the live avatar | dev mode: placeholder avatar + browser speech |
| `SHOPIFY_STORE_DOMAIN` + `SHOPIFY_STOREFRONT_TOKEN` | live price/stock | catalog prices, flagged stale |

Deepgram/ElevenLabs keys are legacy (UE5 path only) — Anam does STT and
TTS itself on the web path.

## The data defines the business

Everything Cigar-specific lives in `data/` (see docs/CONFIG.md): the
persona (her name is in `data/persona.md`), store facts, house rules
(including the subscriber offer + sign-up URL), and the product Excel —
any column in the sheet becomes a live, searchable, recommendable
attribute. Swap the `data/` folder and the same engine sells cars.

## Status

- Engine + catalog + persona: done (carried over).
- Web kiosk (server, UI, Anam custom-LLM integration): built, runs in dev
  mode end-to-end. Waiting on `ANAM_API_KEY` to light up the real avatar.
- Real product Excel from Arman: pending — current data is 50 real
  products pulled from the live Shopify store, recommendation columns empty.
