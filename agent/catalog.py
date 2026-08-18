"""SQLite catalog access + live Shopify price/stock overlay.

Nothing here ever invents a value. Missing field -> key omitted from the dict
that Claude sees, so she cannot read out a number that doesn't exist.

The schema is open: columns the build script didn't recognize live in
product_extra as free-form attributes. They ride along on every result,
they're in the FTS index, and search/recommend can filter and score on
them by name - so a new business with new columns needs no code change.
"""
import os
import re
import time
import sqlite3
import logging
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("catalog")

DB_PATH = Path("data/catalog.db")
_SHOPIFY_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # 5 minutes

FIELDS = [
    "handle", "title", "brand", "category", "price", "size", "ring_gauge",
    "length", "shape", "wrapper", "origin", "strength", "tasting_notes",
    "description", "image_url", "video_url", "model_3d_url", "smoke_minutes",
    "time_of_day", "blood_type", "palate_profile", "occasion", "pairs_with",
]

# Short fields go to Claude; long prose is trimmed so it doesn't eat the budget.
BRIEF_FIELDS = [
    "handle", "title", "brand", "price", "size", "wrapper", "strength",
    "origin", "tasting_notes", "smoke_minutes", "time_of_day", "image_url",
]


def _conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"{DB_PATH} missing - run scripts/build_catalog.py first")
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _clean(row: sqlite3.Row, fields: list[str]) -> dict:
    """Drop NULLs entirely rather than passing empty strings to the model."""
    out = {}
    for f in fields:
        v = row[f] if f in row.keys() else None
        if v is not None and str(v).strip():
            out[f] = str(v).strip()
    return out


def _fts_query(q: str) -> str:
    """Sanitize user speech into a safe FTS5 OR-query. Speech is messy."""
    terms = [t for t in re.findall(r"[a-zA-Z0-9]+", q or "") if len(t) > 1]
    return " OR ".join(f"{t}*" for t in terms[:8])


def _norm_key(k: str) -> str:
    """Same normalization as the build script, so 'Gas Mileage' == 'gasmileage'."""
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _attrs(db: sqlite3.Connection, handle: str) -> dict:
    """Free-form attributes for one product, in sheet column order."""
    rows = db.execute(
        "SELECT key, value FROM product_extra WHERE handle = ? ORDER BY rowid",
        (handle,),
    ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def _with_attrs(db: sqlite3.Connection, p: dict, cap: int | None = None) -> dict:
    a = _attrs(db, p.get("handle", ""))
    if cap is not None:
        a = dict(list(a.items())[:cap])
    if a:
        p["attributes"] = a
    return p


# ---------------------------------------------------------------- search

def search_products(
    query: str = "",
    filters: Optional[dict] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    limit: int = 5,
    **legacy,
) -> list[dict]:
    """`filters` matches ANY column: fixed ones (category, brand...) or the
    sheet's own attribute columns (gas mileage, wrapper, anything)."""
    filters = dict(filters or {})
    for k, v in legacy.items():          # old-style category=/strength=/wrapper= still work
        if v is not None:
            filters[k] = v

    db = _conn()
    try:
        where, params = [], []

        if query and (fts := _fts_query(query)):
            sql = ("SELECT p.* FROM products_fts f JOIN products p ON p.handle = f.handle "
                   "WHERE products_fts MATCH ?")
            params.append(fts)
        else:
            sql = "SELECT p.* FROM products p WHERE 1=1"

        fixed = {_norm_key(f): f for f in FIELDS}
        for key, val in filters.items():
            if val is None or not str(val).strip():
                continue
            nk = _norm_key(key)
            if nk in fixed:
                where.append(f"LOWER(p.{fixed[nk]}) LIKE ?")
                params.append(f"%{str(val).lower()}%")
            else:
                where.append("EXISTS (SELECT 1 FROM product_extra e WHERE e.handle = p.handle "
                             "AND e.key_norm = ? AND LOWER(e.value) LIKE ?)")
                params += [nk, f"%{str(val).lower()}%"]
        if price_min is not None:
            where.append("CAST(REPLACE(REPLACE(p.price,'$',''),',','') AS REAL) >= ?"); params.append(price_min)
        if price_max is not None:
            where.append("CAST(REPLACE(REPLACE(p.price,'$',''),',','') AS REAL) <= ?"); params.append(price_max)

        if where:
            sql += " AND " + " AND ".join(where)
        if query:
            sql += " ORDER BY rank"
        sql += " LIMIT ?"
        params.append(min(limit, 10))

        rows = db.execute(sql, params).fetchall()
        return [_with_attrs(db, _clean(r, BRIEF_FIELDS), cap=4) for r in rows]
    except sqlite3.OperationalError as e:
        log.warning("search failed (%s) - falling back to LIKE", e)
        rows = db.execute(
            "SELECT * FROM products WHERE LOWER(title) LIKE ? LIMIT ?",
            (f"%{(query or '').lower()}%", limit),
        ).fetchall()
        return [_clean(r, BRIEF_FIELDS) for r in rows]
    finally:
        db.close()


def get_product(handle: str) -> Optional[dict]:
    db = _conn()
    try:
        row = db.execute("SELECT * FROM products WHERE handle = ?", (handle,)).fetchone()
        if not row:
            return None
        p = _clean(row, FIELDS)
        if p.get("description") and len(p["description"]) > 600:
            p["description"] = p["description"][:600] + "..."
        p["has_video"] = "video_url" in p
        p["has_model_3d"] = "model_3d_url" in p
        _with_attrs(db, p)               # full detail carries every attribute
        p.update(_shopify_overlay(handle, fallback_price=p.get("price")))
        return p
    finally:
        db.close()


def get_collection(handle: str, limit: int = 12) -> list[dict]:
    db = _conn()
    try:
        rows = db.execute(
            "SELECT * FROM products WHERE LOWER(category) LIKE ? LIMIT ?",
            (f"%{handle.lower().replace('-', ' ')}%", limit),
        ).fetchall()
        return [_with_attrs(db, _clean(r, BRIEF_FIELDS), cap=4) for r in rows]
    finally:
        db.close()


def recommend(
    criteria: Optional[dict] = None,
    budget_max: Optional[float] = None,
    limit: int = 3,
    **legacy,
) -> list[dict]:
    """Score products against ANY of the sheet's columns. `criteria` is
    free-form: {"time of day": "evening", "palate": "sweet"} for cigars,
    {"car type": "suv", "gas mileage": "hybrid"} for cars. A criterion whose
    name matches a column scores strongly on that column; otherwise its value
    is matched against everything the product says about itself. Pure
    matching, no model guessing."""
    criteria = dict(criteria or {})
    for k, v in legacy.items():          # old-style time_of_day=/palate= still work
        if v is not None:
            criteria[k] = v

    db = _conn()
    try:
        rows = db.execute("SELECT * FROM products").fetchall()
        scored = []
        for r in rows:
            fields = _clean(r, FIELDS)
            attrs = _attrs(db, r["handle"])
            by_key = {_norm_key(k): v for k, v in {**fields, **attrs}.items()}
            haystack = " ".join(str(v) for v in by_key.values()).lower()

            score = 0
            for key, want in criteria.items():
                if want is None or not str(want).strip():
                    continue
                want_l = str(want).lower()
                have = by_key.get(_norm_key(key))
                if have is not None and want_l in str(have).lower():
                    score += 3                       # named column agrees
                elif want_l in haystack:
                    score += 1                       # mentioned somewhere on the product
            if budget_max and fields.get("price"):
                try:
                    if float(re.sub(r"[^\d.]", "", fields["price"])) <= budget_max:
                        score += 1
                    else:
                        score -= 5
                except ValueError:
                    pass
            if score > 0:
                scored.append((score, r))

        scored.sort(key=lambda x: -x[0])
        return [_with_attrs(db, _clean(r, BRIEF_FIELDS), cap=4) for _, r in scored[:limit]]
    finally:
        db.close()


# ---------------------------------------------------------------- shopify

def _shopify_overlay(handle: str, fallback_price: Optional[str] = None) -> dict:
    """Live price + stock. On failure, flag the price as stale so she hedges."""
    domain = os.getenv("SHOPIFY_STORE_DOMAIN")
    token = os.getenv("SHOPIFY_STOREFRONT_TOKEN")
    if not (domain and token):
        return {"price_stale": True} if fallback_price else {}

    now = time.time()
    if (hit := _SHOPIFY_CACHE.get(handle)) and now - hit[0] < _CACHE_TTL:
        return hit[1]

    q = """
    query($handle: String!) {
      product(handle: $handle) {
        availableForSale
        priceRange { minVariantPrice { amount currencyCode } }
      }
    }"""
    try:
        r = httpx.post(
            f"https://{domain}/api/2024-10/graphql.json",
            headers={"X-Shopify-Storefront-Access-Token": token},
            json={"query": q, "variables": {"handle": handle}},
            timeout=2.5,
        )
        r.raise_for_status()
        p = (r.json().get("data") or {}).get("product")
        if not p:
            return {"price_stale": True}
        amt = p["priceRange"]["minVariantPrice"]["amount"]
        out = {
            "price": f"${float(amt):,.2f}",
            "in_stock": bool(p["availableForSale"]),
            "price_stale": False,
        }
        _SHOPIFY_CACHE[handle] = (now, out)
        return out
    except Exception as e:
        log.warning("Shopify overlay failed for %s: %s", handle, e)
        return {"price_stale": True}


if __name__ == "__main__":
    import sys, json
    print(json.dumps(search_products(" ".join(sys.argv[1:]) or "maduro"), indent=2))
