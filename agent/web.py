"""Web kiosk server — the online successor to the UE5 path.

One FastAPI app does three jobs:

1. Serves the kiosk front end (web/) and the product data API it needs.
2. Mints Anam session tokens server-side, so ANAM_API_KEY never reaches
   the browser. The Anam persona is created with llmId CUSTOMER_CLIENT_V1,
   which turns Anam's own LLM OFF — Anam does avatar video, STT and TTS
   lip-sync; Claude (agent/brain.py) stays the only brain.
3. Speaks docs/WEB.md over a WebSocket: transcripts and touches come in,
   sentences and scene commands go out. The Brain is reused untouched.

Run:  uvicorn agent.web:app --host 0.0.0.0 --port 8000
"""
import os
import re
import json
import time
import sqlite3
import asyncio
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import catalog
from .brain import Brain, load_house_rules, parse_signup_url, PERSONA

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s")
log = logging.getLogger("web")

WEB_DIR = Path(__file__).parent.parent / "web"
MODELS_DIR = Path("data/models")

SILENCE_TIMEOUT = 90          # kiosk: customer may be reading a menu; be patient
MAX_SESSION = 900

ANAM_TOKEN_URL = "https://api.anam.ai/v1/auth/session-token"
# Docs-default persona (Cara). Override per store in .env.
ANAM_DEFAULT_AVATAR = "30fa96d0-26c4-4e55-94a0-517025942e18"
ANAM_DEFAULT_VOICE = "6bfbe25a-979d-40f3-a92b-5394170af54b"

app = FastAPI(title="Interactive Avatar Kiosk")


# ---------------------------------------------------------------- config

def _persona_name() -> str:
    """The avatar's name, from data/persona.md ('Her name is **Hannah**.')."""
    try:
        text = PERSONA.read_text(encoding="utf-8")
    except OSError:
        return "Ava"
    in_name = False
    for line in text.splitlines():
        if line.strip().lower().startswith("## name"):
            in_name = True
            continue
        if line.startswith("##"):
            in_name = False
        if in_name and (m := re.search(r"\*\*([^*]+)\*\*", line)):
            return m.group(1).strip()
    return "Ava"


def _store_name() -> str:
    """First bold phrase in persona.md's business section, else env, else generic."""
    if env := os.getenv("STORE_NAME"):
        return env
    try:
        text = PERSONA.read_text(encoding="utf-8")
    except OSError:
        return "Welcome"
    in_biz = False
    for line in text.splitlines():
        if line.strip().lower().startswith("## the business"):
            in_biz = True
            continue
        if line.startswith("##"):
            in_biz = False
        if in_biz and (m := re.search(r"\*\*([^*]+)\*\*", line)):
            return m.group(1).strip()
    return "Welcome"


@app.get("/api/config")
def get_config():
    return {
        "store_name": _store_name(),
        "persona_name": _persona_name(),
        "anam_enabled": bool(os.getenv("ANAM_API_KEY")),
        "brain_enabled": bool(os.getenv("ANTHROPIC_API_KEY")),
        "signup_url": parse_signup_url(load_house_rules()),
        # Public storefront domain for cart / product links shown to customers.
        # SHOPIFY_STORE_DOMAIN is the *.myshopify.com admin domain, which is not
        # what we want a customer scanning a QR code to land on.
        "store_domain": os.getenv("PUBLIC_STORE_DOMAIN", "cigarinc.com"),
    }


# ---------------------------------------------------------------- anam

@app.post("/api/anam-session-token")
async def anam_session_token():
    """Mint a short-lived Anam session token. The API key stays on the server."""
    api_key = os.getenv("ANAM_API_KEY")
    if not api_key:
        raise HTTPException(503, "ANAM_API_KEY is not set — kiosk runs in dev mode")

    persona = {
        "name": _persona_name(),
        "avatarId": os.getenv("ANAM_AVATAR_ID", ANAM_DEFAULT_AVATAR),
        "voiceId": os.getenv("ANAM_VOICE_ID", ANAM_DEFAULT_VOICE),
        "llmId": "CUSTOMER_CLIENT_V1",   # Anam's own LLM off; Claude is the brain
    }
    if model := os.getenv("ANAM_AVATAR_MODEL"):
        persona["avatarModel"] = model

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            ANAM_TOKEN_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"personaConfig": persona},
        )
    if r.status_code != 200:
        log.error("Anam token mint failed %s: %s", r.status_code, r.text[:300])
        raise HTTPException(502, "Could not create Anam session")
    return r.json()


# ---------------------------------------------------------------- catalog api

def _db() -> sqlite3.Connection:
    c = sqlite3.connect(catalog.DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


@app.get("/api/menus")
def get_menus():
    """Root of the telescoping menu, derived live from the catalog — a new
    Excel with new categories reshapes the menu with no code change."""
    db = _db()
    try:
        cats = [r[0] for r in db.execute(
            "SELECT DISTINCT category FROM products "
            "WHERE category IS NOT NULL AND TRIM(category) != '' ORDER BY category")]
        brands = [r[0] for r in db.execute(
            "SELECT DISTINCT brand FROM products "
            "WHERE brand IS NOT NULL AND TRIM(brand) != '' ORDER BY brand")]
        showpieces = db.execute(
            "SELECT COUNT(*) FROM products "
            "WHERE video_url IS NOT NULL OR model_3d_url IS NOT NULL").fetchone()[0]
    finally:
        db.close()
    menu = {
        "categories": cats,
        "brands": brands,
        "showpieces": showpieces,   # products with a video or 3D model
    }
    return menu


@app.get("/api/products")
def get_products(category: str | None = None, brand: str | None = None,
                 showpieces: bool = False, q: str | None = None, limit: int = 24):
    db = _db()
    try:
        where, params = [], []
        if category:
            where.append("LOWER(category) = ?"); params.append(category.lower())
        if brand:
            where.append("LOWER(brand) = ?"); params.append(brand.lower())
        if showpieces:
            where.append("(video_url IS NOT NULL OR model_3d_url IS NOT NULL)")
        if q:
            where.append("LOWER(title) LIKE ?"); params.append(f"%{q.lower()}%")
        sql = "SELECT * FROM products"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY title LIMIT ?"
        params.append(min(limit, 60))
        rows = db.execute(sql, params).fetchall()
        out = []
        for r in rows:
            p = catalog._clean(r, catalog.BRIEF_FIELDS)
            p["has_video"] = bool(r["video_url"])
            p["has_model_3d"] = bool(r["model_3d_url"])
            out.append(p)
        return {"products": out}
    finally:
        db.close()


@app.get("/api/product/{handle}")
def get_product(handle: str):
    p = catalog.get_product(handle)
    if not p:
        raise HTTPException(404, f"no product '{handle}'")
    return p


# ---------------------------------------------------------------- ws session

class WebSession:
    """One kiosk browser. Mirrors agent/server.py's Session, minus the audio
    plumbing — Anam owns the customer's ears and her voice now."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.state = "attract"
        self.active = False
        self.speaking = False
        self.started = 0.0
        self.last_activity = time.time()
        self.brain: Brain | None = None
        self._watchdog: asyncio.Task | None = None

    async def _send(self, obj: dict):
        try:
            await self.ws.send_text(json.dumps(obj))
        except Exception:
            pass

    async def set_state(self, value: str):
        if value != self.state:
            self.state = value
            await self._send({"type": "state", "value": value})

    async def _emit_ui(self, action: str, payload: dict):
        await self._send({"type": "ui", "action": action, "payload": payload})
        if action in ("show_product_frame", "show_media"):
            await self.set_state("presenting")

    async def _speak(self, text: str):
        self.speaking = True
        log.info("HER: %s", text)
        await self._send({"type": "speak", "text": text})
        self.last_activity = time.time()

    async def _end_utterance(self):
        if self.speaking:
            self.speaking = False
            await self._send({"type": "speak_end"})

    # ------------------------------------------------------------ lifecycle

    def _ensure_brain(self) -> bool:
        if self.brain:
            return True
        if not os.getenv("ANTHROPIC_API_KEY"):
            return False
        self.brain = Brain(emit_ui=self._emit_ui, speak=self._speak,
                           set_state=self.set_state)
        return True

    async def wake(self, trigger: str = "touch"):
        if self.active:
            return
        log.info("waking (%s)", trigger)
        self.active = True
        self.started = time.time()
        self.last_activity = time.time()
        await self.set_state("waking")

        if not self._ensure_brain():
            await self._send({"type": "error", "code": "brain_offline",
                              "message": "ANTHROPIC_API_KEY not set"})
            await self.set_state("listening")
            return

        self.brain.reset()
        self._watchdog = asyncio.create_task(self._watch())
        await self.brain.greet()
        await self._end_utterance()
        await self.set_state("listening")

    async def sleep(self, reason: str = "timeout", farewell: bool = True):
        if not self.active:
            return
        log.info("sleeping (%s)", reason)
        self.active = False
        await self.set_state("farewell")
        if farewell and self.brain:
            await self._speak("I'll be right here whenever you're ready.")
            await self._end_utterance()
        if self._watchdog:
            self._watchdog.cancel()
        await self._emit_ui("close_overlay", {})
        await self.set_state("attract")

    async def _watch(self):
        try:
            while True:
                await asyncio.sleep(2)
                if time.time() - self.last_activity > SILENCE_TIMEOUT:
                    return await self.sleep("silence")
                if time.time() - self.started > MAX_SESSION:
                    return await self.sleep("max_session")
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------ inbound

    async def on_message(self, msg: dict):
        t = msg.get("type")
        self.last_activity = time.time()

        if t == "hello":
            log.info("kiosk connected: %s", msg.get("client"))
            await self.set_state("attract")

        elif t == "wake":
            await self.wake(msg.get("trigger", "touch"))

        elif t == "sleep":
            await self.sleep(msg.get("reason", "manual"))

        elif t == "user_text":
            text = (msg.get("text") or "").strip()
            if not text:
                return
            if not self.active:
                await self.wake("speech")
            if not self.brain:
                return
            log.info("THEM: %s", text)
            await self.brain.respond(text)
            await self._end_utterance()
            await self.set_state("listening")

        elif t == "interrupted":
            # Anam already cut her audio; just note it so the next turn is clean.
            log.info("--- barge-in (client) ---")
            self.speaking = False
            await self.set_state("listening")

        elif t == "touch":
            if not self.active:
                await self.wake("touch")
            if not self.brain:
                return
            target, tid = msg.get("target"), msg.get("id") or msg.get("handle")
            close_prompt = (
                "[The customer closed the sign-up page. Pick the conversation back up "
                "warmly where you left off - don't quiz them on whether they signed up.]"
                if tid == "signup" else
                "[The customer closed the panel. Acknowledge briefly and offer the next thing.]"
            )
            prompt = {
                "menu":          f"[The customer tapped the '{tid}' menu. React and guide them.]",
                "product_grid":  f"[The customer tapped product '{tid}'. Show its frame and say something about it.]",
                "product_frame": f"[The customer tapped the frame for '{tid}'. Open its detail and elaborate.]",
                "close":         close_prompt,
                "mic":           "[The customer tapped the mic. Invite them to speak.]",
            }.get(target)
            if prompt:
                await self.brain.respond(prompt)
                await self._end_utterance()
                await self.set_state("listening")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    session = WebSession(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                await session.on_message(json.loads(raw))
            except json.JSONDecodeError:
                log.warning("bad JSON from kiosk: %r", raw[:200])
            except Exception:
                log.exception("error handling kiosk message")
    except WebSocketDisconnect:
        pass
    finally:
        log.info("kiosk disconnected")
        await session.sleep("disconnect", farewell=False)


# ---------------------------------------------------------------- startup

@app.on_event("startup")
def link_local_models():
    """Any data/models/<handle>.glb becomes that product's 3D model, served
    at /models/. Drop a file in, restart, and 'View in 3D' lights up."""
    if not (MODELS_DIR.exists() and catalog.DB_PATH.exists()):
        return
    db = _db()
    try:
        for glb in MODELS_DIR.glob("*.glb"):
            n = db.execute(
                "UPDATE products SET model_3d_url = ? "
                "WHERE handle = ? AND (model_3d_url IS NULL OR model_3d_url = '')",
                (f"/models/{glb.name}", glb.stem),
            ).rowcount
            if n:
                log.info("linked 3D model %s", glb.name)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------- static

@app.get("/healthz")
def healthz():
    return {"ok": True, "catalog": catalog.DB_PATH.exists()}


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


if MODELS_DIR.exists():
    app.mount("/models", StaticFiles(directory=MODELS_DIR), name="models")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


# ---------------------------------------------------------------- subpath
# Hosting under a path (e.g. https://example.com/hannah/) instead of the domain
# root: set APP_BASE_PATH=/hannah. The whole app - pages, /api, /ws, /static -
# moves under that prefix, so a plain reverse proxy that forwards the path
# untouched is enough; no path rewriting needed. The front end discovers the
# prefix on its own from its module URL, so nothing else has to be configured.
# Unset (the default) = served at the domain root, exactly as before.

BASE_PATH = "/" + os.getenv("APP_BASE_PATH", "").strip().strip("/")

if len(BASE_PATH) > 1:
    from starlette.applications import Starlette
    from starlette.responses import RedirectResponse
    from starlette.routing import Mount, Route

    _kiosk = app

    async def _to_slash(request):
        # /hannah -> /hannah/ so the page's relative asset URLs resolve.
        return RedirectResponse(BASE_PATH + "/", status_code=307)

    app = Starlette(routes=[
        Route(BASE_PATH, _to_slash),
        Mount(BASE_PATH, app=_kiosk),
    ])
    log.info("Kiosk mounted under %s/", BASE_PATH)
