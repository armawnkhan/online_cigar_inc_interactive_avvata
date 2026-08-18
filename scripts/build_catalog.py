"""Build data/catalog.db from the product Excel.

Usage:  python scripts/build_catalog.py data/products.xlsx

Never fabricates a value. Columns the engine doesn't recognize become
free-form ATTRIBUTES: stored per product, full-text searchable, shown to
the model, and usable by the recommend tool. Sell cars tomorrow - add a
'gas mileage' column and she reads it. No code change.
"""
import sys
import re
import math
import sqlite3
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("catalog")

DB_PATH = Path("data/catalog.db")

# canonical field -> accepted header variants (normalized)
FIELD_ALIASES = {
    "handle":          ["handle", "producthandle", "shopifyhandle", "slug", "url", "sku"],
    "title":           ["title", "name", "productname", "product"],
    "brand":           ["brand", "manufacturer", "make", "company"],
    "category":        ["category", "type", "producttype", "collection"],
    "price":           ["price", "retailprice", "msrp", "singlestickprice"],
    "size":            ["size", "vitola", "format"],
    "ring_gauge":      ["ringgauge", "ring", "gauge"],
    "length":          ["length", "lengthinches", "lengthin", "len"],
    "shape":           ["shape", "vitolashape"],
    "wrapper":         ["wrapper", "wrapperleaf", "wrappercolor"],
    "origin":          ["origin", "country", "countryoforigin", "madein"],
    "strength":        ["strength", "body", "intensity"],
    "tasting_notes":   ["tastingnotes", "flavor", "flavornotes", "notes", "profile"],
    "description":     ["description", "desc", "about", "details"],
    "image_url":       ["imageurl", "image", "photo", "mainimage", "img"],
    "video_url":       ["videourl", "video"],
    "model_3d_url":    ["model3durl", "model3d", "glb", "glburl", "3dmodel"],
    "smoke_minutes":   ["smokeminutes", "smokingtime", "duration", "burntime"],
    # recommendation columns
    "time_of_day":     ["timeofday", "besttime", "occasiontime"],
    "blood_type":      ["bloodtype", "bloodtypeplayful", "blood"],
    "palate_profile":  ["palateprofile", "palate", "tasteprofile", "appetite"],
    "occasion":        ["occasion", "bestfor", "pairsoccasion"],
    "pairs_with":      ["pairswith", "pairing", "drinkpairing", "pairs"],
}

# Only a title is truly required; a missing handle is derived from it per-row.
REQUIRED = ["title"]
FTS_FIELDS = ["title", "brand", "tasting_notes", "description", "wrapper", "origin"]


def norm(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


def map_columns(df):
    """Return (canonical -> real column name), plus the list of unmapped columns."""
    lookup = {}
    for col in df.columns:
        lookup.setdefault(norm(col), col)

    mapping, used = {}, set()
    for field, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                mapping[field] = lookup[alias]
                used.add(lookup[alias])
                break

    unmapped = [c for c in df.columns if c not in used]
    return mapping, unmapped


def clean(v):
    """NULL rather than a guess. Empty strings and NaN both become None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan", "n/a", "-", "none") else None


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(title).lower()).strip("-")
    return s or "untitled"


# ------------------------------------------------------------------ derived
# Two values the sheet doesn't have to carry, because they follow from the
# cigar's own dimensions and strength. Both are only ever FILLED IN when the
# sheet leaves them blank - a real value in the Excel always wins.

# Strength on the 1-5 scale: 1 mild ... 5 full.
STRENGTH_SCALE = {
    "mild": 1, "mild-medium": 2, "mild to medium": 2, "mild/medium": 2,
    "medium": 3,
    "medium-full": 4, "medium to full": 4, "medium/full": 4,
    "full": 5,
}

# When that strength is best enjoyed.
TIME_BY_SCALE = {
    1: "Morning, with coffee",
    2: "Morning, with coffee",
    3: "Midday, Afternoon",
    4: "After lunch, After dinner",
    5: "After lunch, After dinner",
}

CU_IN_TO_ML = 16.387064          # 1 cubic inch = 16.387064 cm3 (mL)
_UNICODE_FRAC = {"½": .5, "¼": .25, "¾": .75, "⅓": 1/3, "⅔": 2/3, "⅛": .125,
                 "⅜": .375, "⅝": .625, "⅞": .875}


def _num(v):
    """Parse 6, 6.75, '6 1/8', '6¾' -> float. None if it isn't a number."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    total = 0.0
    for ch, val in _UNICODE_FRAC.items():
        if ch in s:
            total += val
            s = s.replace(ch, " ")
    m = re.match(r"\s*(\d+(?:\.\d+)?)(?:\s+(\d+)\s*/\s*(\d+))?", s)
    if not m:
        return total or None
    total += float(m.group(1))
    if m.group(2) and m.group(3) and float(m.group(3)):
        total += float(m.group(2)) / float(m.group(3))
    return total or None


def smoke_minutes(length, ring_gauge):
    """Minutes to smoke, from the volume of tobacco in the cigar.

    A ring gauge is 1/64 inch of DIAMETER, so radius = ring/128 inches.
    Volume = pi * r^2 * length  (cubic inches) -> mL.
    Reference: a 6 x 52 Toro is 50.98 mL and smokes in ~51 minutes,
    so 1 mL == 1 minute.
    """
    ln, rg = _num(length), _num(ring_gauge)
    if not ln or not rg or ln <= 0 or rg <= 0:
        return None
    volume_ml = math.pi * (rg / 128.0) ** 2 * ln * CU_IN_TO_ML
    return str(int(round(volume_ml)))


def time_of_day_for(strength):
    """Mild smokes with morning coffee; full waits until after dinner."""
    if not strength:
        return None
    key = re.sub(r"\s*-\s*", "-", str(strength).strip().lower())
    scale = STRENGTH_SCALE.get(key)
    return TIME_BY_SCALE.get(scale) if scale else None


def main(xlsx_path: str):
    src = Path(xlsx_path)
    if not src.exists():
        log.error("No such file: %s", src)
        sys.exit(1)

    xl = pd.ExcelFile(src)
    sheet = "Products" if "Products" in xl.sheet_names else 0
    df = pd.read_excel(xl, sheet_name=sheet, dtype=object)

    # The recommendation workbook has a source-tag row (shopify/manual/derived)
    # directly under the headers. Drop it if present.
    if len(df) and set(df.iloc[0].dropna().astype(str)) <= {"shopify", "manual", "derived"}:
        df = df.iloc[1:].reset_index(drop=True)
        log.info("Dropped source-tag row under headers")

    log.info("Read %d rows, %d columns from %s (sheet: %s)", len(df), len(df.columns), src.name, sheet)

    mapping, unmapped = map_columns(df)

    missing_required = [f for f in REQUIRED if f not in mapping]
    if missing_required:
        log.error("Cannot build catalog. Missing required column(s): %s", missing_required)
        log.error("Headers found: %s", list(df.columns))
        sys.exit(1)

    # These are computed below when the sheet doesn't carry them, so a missing
    # column is normal rather than a problem worth warning about.
    DERIVED = {"smoke_minutes", "time_of_day"}
    for field in FIELD_ALIASES:
        if field not in mapping and field not in DERIVED:
            log.warning("No column mapped to '%s' -> will be NULL for every product", field)
    if unmapped:
        log.info("Free-form attribute columns (searchable, shown to the model): %s", unmapped)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    db = sqlite3.connect(DB_PATH)

    cols = list(FIELD_ALIASES.keys())
    db.execute(f"CREATE TABLE products ({', '.join(f'{c} TEXT' for c in cols)}, PRIMARY KEY (handle))")
    # key_norm lets the agent filter on "Gas Mileage" however Claude spells it
    db.execute("CREATE TABLE product_extra (handle TEXT, key TEXT, key_norm TEXT, value TEXT)")
    db.execute(
        f"CREATE VIRTUAL TABLE products_fts USING fts5("
        f"{', '.join(FTS_FIELDS)}, extra, handle UNINDEXED, tokenize='porter unicode61')"
    )

    inserted, skipped = 0, 0
    derived_minutes, derived_time = 0, 0
    seen = set()

    if "handle" not in mapping:
        log.warning("No handle column - handles will be derived from titles")

    for _, row in df.iterrows():
        rec = {f: clean(row.get(col)) for f, col in mapping.items()}
        for f in cols:
            rec.setdefault(f, None)

        if not rec["title"]:
            skipped += 1
            continue
        if not rec["handle"]:
            rec["handle"] = slugify(rec["title"])
            log.warning("Row '%s' had no handle; derived '%s'", rec["title"], rec["handle"])

        if rec["handle"] in seen:
            log.warning("Duplicate handle '%s' - keeping first", rec["handle"])
            skipped += 1
            continue
        seen.add(rec["handle"])

        # Derived, never overriding a real value from the sheet.
        if not rec["smoke_minutes"]:
            if (mins := smoke_minutes(rec["length"], rec["ring_gauge"])):
                rec["smoke_minutes"] = mins
                derived_minutes += 1
        if not rec["time_of_day"]:
            if (tod := time_of_day_for(rec["strength"])):
                rec["time_of_day"] = tod
                derived_time += 1

        db.execute(
            f"INSERT INTO products ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [rec[c] for c in cols],
        )
        extras = []
        for col in unmapped:
            val = clean(row.get(col))
            if val:
                db.execute("INSERT INTO product_extra VALUES (?,?,?,?)",
                           (rec["handle"], str(col), norm(col), val))
                extras.append(f"{col} {val}")
        db.execute(
            f"INSERT INTO products_fts ({', '.join(FTS_FIELDS)}, extra, handle) "
            f"VALUES ({', '.join('?' * (len(FTS_FIELDS) + 2))})",
            [rec[f] or "" for f in FTS_FIELDS] + [" ".join(extras), rec["handle"]],
        )
        inserted += 1

    db.execute("CREATE INDEX idx_category ON products(category)")
    db.execute("CREATE INDEX idx_brand ON products(brand)")
    db.execute("CREATE INDEX idx_strength ON products(strength)")
    db.execute("CREATE INDEX idx_extra_handle ON product_extra(handle)")
    db.execute("CREATE INDEX idx_extra_key ON product_extra(key_norm)")
    db.commit()

    log.info("Wrote %d products to %s (%d rows skipped)", inserted, DB_PATH, skipped)
    log.info("Derived smoke_minutes for %d products (volume of tobacco, 1 mL = 1 min)", derived_minutes)
    log.info("Derived time_of_day for %d products (from strength, 1-5 scale)", derived_time)

    with_media = db.execute(
        "SELECT COUNT(*) FROM products WHERE model_3d_url IS NOT NULL"
    ).fetchone()[0]
    log.info("%d products have a 3D model", with_media)
    db.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/products.xlsx")
