"""Tool schemas for Claude, and the dispatcher.

Two families:
  DATA tools  - hit the catalog, return facts. Can be slow (Shopify).
  UI tools    - fire a scene command at Unreal, return instantly.

The UI tools are what put the frame in her hands. Claude calls them in the same
turn it speaks, so the gesture and the sentence land together.
"""
import logging
from typing import Callable, Awaitable, Any

from . import catalog

log = logging.getLogger("tools")

# Tools that are fast enough not to need a verbal filler.
FAST_TOOLS = {"show_product_frame", "show_media", "open_menu", "close_overlay",
              "open_signup", "show_humidor_3d"}

TOOL_SCHEMAS = [
    {
        "name": "search_products",
        "description": (
            "Search the store catalog by free text and optional filters. "
            "Use this whenever the customer describes what they want in their own words. "
            "Returns up to 10 products with brief details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free text, in the customer's own words"},
                "filters": {
                    "type": "object",
                    "description": (
                        "Attribute name -> value to match. Works on ANY column the store's "
                        "product sheet has - e.g. {\"category\": \"humidor\"}, "
                        "{\"wrapper\": \"maduro\"}, {\"gas mileage\": \"hybrid\"}. "
                        "Product results show which attributes exist."
                    ),
                },
                "price_min": {"type": "number"},
                "price_max": {"type": "number"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "recommend",
        "description": (
            "Rank products against the store's own product data. Pass what you've learned "
            "about the customer as criteria; each criterion is matched against the product "
            "sheet's own columns, whatever they are for this store. Prefer this over "
            "search_products once you know something about the customer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "criteria": {
                    "type": "object",
                    "description": (
                        "Attribute name -> what the customer wants, e.g. "
                        "{\"time of day\": \"evening\", \"palate\": \"sweet\"} or "
                        "{\"car type\": \"suv\", \"fuel\": \"hybrid\"}. Use attribute names "
                        "you have seen on this store's products."
                    ),
                },
                "budget_max": {"type": "number"},
                "limit": {"type": "integer", "default": 3},
            },
        },
    },
    {
        "name": "get_product",
        "description": (
            "Full detail for one product by handle, including live price and stock, and "
            "whether it has a video or a 3D model. Call this before quoting any price."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"handle": {"type": "string"}},
            "required": ["handle"],
        },
    },
    {
        "name": "get_collection",
        "description": "List products in a collection, e.g. 'humidors', 'accessories', 'opus-x'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "limit": {"type": "integer", "default": 12},
            },
            "required": ["handle"],
        },
    },
    # ---------------- UI ----------------
    {
        "name": "show_product_frame",
        "description": (
            "Put a product in your hands as a framed card the customer can see and touch. "
            "Call this EVERY time you name a specific product out loud, in the same turn. "
            "One product at a time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"handle": {"type": "string"}},
            "required": ["handle"],
        },
    },
    {
        "name": "show_media",
        "description": (
            "Show a product's images, video, or 3D model at full size. Only call this for "
            "products where get_product reported has_video or has_model_3d. Offer it in "
            "words first - 'I can show you this one in three dimensions' - then call it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string"},
                "kind": {"type": "string", "enum": ["images", "video", "model_3d"]},
            },
            "required": ["handle", "kind"],
        },
    },
    {
        "name": "show_humidor_3d",
        "description": (
            "Show a humidor as a real 3D object the customer can spin with their hands and "
            "open the lid of. This is the showpiece moment - offer it in words first "
            "(\"this one's worth seeing properly, let me show you\") and then call it. "
            "Only for the humidors listed in the model enum; everything else has no 3D."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Which humidor to show.",
                    "enum": [
                        "opusx-black", "opusx-blue", "opusx-red",
                        "macc-01", "macc-02", "macc-03", "macc-04", "macc-05",
                        "macc-06", "macc-07", "macc-08", "macc-09", "macc-10",
                    ],
                },
                "open_lid": {
                    "type": "boolean",
                    "description": "Start with the lid open. Use when showing what is inside.",
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "open_menu",
        "description": "Open a browsing menu, e.g. 'cigars', 'humidors', 'accessories', 'deals'.",
        "input_schema": {
            "type": "object",
            "properties": {"menu_id": {"type": "string"}},
            "required": ["menu_id"],
        },
    },
    {
        "name": "close_overlay",
        "description": "Dismiss whatever is on screen and return to a clear view of yourself.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "open_signup",
        "description": (
            "Open the store's email sign-up page in a floating frame the customer can "
            "fill in on the spot. ONLY call this after the customer has said yes to joining "
            "the list - never unprompted. Offer it in words first, at most once per "
            "conversation. They close the frame themselves when they're done."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


class ToolRunner:
    """`emit_ui` is injected by the server: an async fn (action, payload) -> None.
    `signup_url` comes from the house rules file; None disables open_signup.
    `viewer_3d_url` is the hosted 3D humidor viewer; None disables show_humidor_3d."""

    def __init__(self, emit_ui: Callable[[str, dict], Awaitable[None]],
                 signup_url: str | None = None,
                 viewer_3d_url: str | None = None):
        self.emit_ui = emit_ui
        self.signup_url = signup_url
        self.viewer_3d_url = viewer_3d_url
        self._last_frame: str | None = None

    async def run(self, name: str, args: dict) -> Any:
        try:
            handler = getattr(self, f"_{name}")
        except AttributeError:
            return {"error": f"unknown tool {name}"}
        try:
            return await handler(**args)
        except TypeError as e:
            log.warning("bad args for %s: %s", name, e)
            return {"error": f"bad arguments: {e}"}
        except Exception as e:
            log.exception("tool %s failed", name)
            return {"error": str(e)}

    # -------- data --------
    async def _search_products(self, **kw):
        results = catalog.search_products(**kw)
        return {"results": results, "count": len(results)} if results else {
            "results": [],
            "note": "Nothing matched. Ask a clarifying question rather than guessing.",
        }

    async def _recommend(self, **kw):
        results = catalog.recommend(**kw)
        return {"results": results} if results else {
            "results": [],
            "note": "No scored match. Fall back to search_products, or ask what they usually enjoy.",
        }

    async def _get_product(self, handle: str):
        p = catalog.get_product(handle)
        if not p:
            return {"error": f"No product with handle '{handle}'. Do not describe it - say you'll check."}
        return p

    async def _get_collection(self, handle: str, limit: int = 12):
        return {"results": catalog.get_collection(handle, limit)}

    # -------- ui --------
    async def _show_product_frame(self, handle: str):
        p = catalog.get_product(handle)
        if not p:
            return {"ok": False, "error": f"unknown handle '{handle}'"}
        payload = {
            k: p.get(k) for k in
            ("handle", "title", "brand", "price", "image_url", "size", "wrapper", "strength")
            if p.get(k)
        }
        # up to four sheet-defined attributes ride along for display
        if attrs := dict(list((p.get("attributes") or {}).items())[:4]):
            payload["attributes"] = attrs
        payload["has_video"] = p.get("has_video", False)
        payload["has_model_3d"] = p.get("has_model_3d", False)
        payload["swap"] = self._last_frame is not None
        self._last_frame = handle
        await self.emit_ui("show_product_frame", payload)
        return {"ok": True}

    async def _show_media(self, handle: str, kind: str):
        p = catalog.get_product(handle)
        if not p:
            return {"ok": False, "error": f"unknown handle '{handle}'"}
        url = {
            "video": p.get("video_url"),
            "model_3d": p.get("model_3d_url"),
            "images": p.get("image_url"),
        }.get(kind)
        if not url:
            return {"ok": False, "error": f"'{handle}' has no {kind}. Do not offer it."}
        await self.emit_ui("show_media", {"handle": handle, "kind": kind, "url": url})
        return {"ok": True}

    async def _open_menu(self, menu_id: str):
        await self.emit_ui("open_menu", {"menu_id": menu_id})
        return {"ok": True}

    async def _close_overlay(self):
        self._last_frame = None
        await self.emit_ui("close_overlay", {})
        return {"ok": True}

    HUMIDOR_TITLES = {
        "opusx-black": "Fuente OpusX - black leather humidor",
        "opusx-blue":  "Fuente OpusX - blue leather humidor",
        "opusx-red":   "Fuente OpusX - red leather humidor",
    }

    async def _show_humidor_3d(self, model: str, open_lid: bool = False):
        """Open the hosted 3D humidor viewer, pointed at one specific piece.

        The viewer is a separate static site (500 MB of models - far too much to
        ship with the kiosk), so this hands the browser a URL rather than a file.
        """
        if not self.viewer_3d_url:
            return {"ok": False, "error": "no 3D viewer configured"}

        model = (model or "").strip().lower()
        if not model:
            return {"ok": False, "error": "model is required"}

        base = self.viewer_3d_url.rstrip("/") + "/"
        url = f"{base}?model={model}" + ("&open=1" if open_lid else "")
        title = self.HUMIDOR_TITLES.get(model) or f"Maccarrone {model.replace('macc-', '')}"
        await self.emit_ui("show_3d", {"url": url, "model": model, "title": title})
        return {"ok": True, "showing": model}

    async def _open_signup(self):
        if not self.signup_url:
            return {"ok": False,
                    "error": "No sign-up page is configured. Do not offer the list."}
        await self.emit_ui("show_web_page", {
            "id": "signup",
            "url": self.signup_url,
            "title": "Join the list",
        })
        return {"ok": True,
                "note": "Sign-up page is on screen. Keep talking naturally; they'll close it when done."}
