# Adapting the avatar to a new business

The engine is generic. One folder — `data/` — defines the whole business.
To deploy the avatar for a different store, you edit four files and touch
no code:

| File | What it defines | Format |
|---|---|---|
| `data/persona.md` | Who the avatar is: **her name**, the business, her character, how she sells this product category, any category-specific lines she must hold (age limits, claim bans) | Plain English, edit freely |
| `data/store_facts.md` | Every true fact she may state: address, hours, phone, brands, policies. Nothing off this list gets stated | One fact per line |
| `data/house_rules.md` | Operator rules: never/always/redirect lists, the subscriber offer (deal terms **and sign-up URL**), and hard-blocked words | Keep the `##` section headings; edit the lines |
| `data/products.xlsx` | The catalog | Columns below |

All four are read at startup. Edit → restart → she's the new business.
Generic behavior — spoken style, ask-before-recommend, showing frames,
overlay handling, the subscriber-offer choreography — lives in
`agent/engine.md` and is code-owned; don't duplicate it in the persona.

## Things defined by the files, not the code

- **Her name** — the `## Name` line in persona.md.
- **The subscriber deal and its URL** — the "The subscriber offer" section of
  house_rules.md. The `open_signup` tool opens whatever URL is on its own
  line there. Empty section = she never mentions a list, and the tool refuses.
- **Hard-banned phrases** — the "Never say these words" section feeds a
  code-level output filter, not just the prompt. (A built-in health-claims
  filter — "safer than", "medicinal", etc. — is always on; appropriate for
  any regulated product.)

## Connecting a commerce platform

The catalog pipeline is platform-agnostic by design:

```
any platform → data/products.xlsx → scripts/build_catalog.py → data/catalog.db
```

`products.xlsx` is the universal exchange format, and **the columns are
open**. Whatever the source — Shopify, WooCommerce, Magento, a POS export,
a hand-made sheet — only these column meanings are reserved (header naming
is fuzzy-matched, "Product Name" works for title):

- `handle` (unique id — derived from title if absent) and `title` — required
- `price`, `brand`, `category`, `description` — recognized when present
- `image_url`, `video_url`, `model_3d_url` — power the visual frames

**Every other column becomes a free-form attribute automatically**: stored
per product, full-text searchable, shown to the model on every result, and
usable by name in `search_products` filters and `recommend` criteria. A car
sheet with `Car Type`, `Gas Mileage`, `Drivetrain` needs no code change —
she reads them, searches them ("something economical" finds the hybrid),
filters on them, and recommends by them. Attribute matching is text
contains, case-insensitive; numeric *range* queries exist only for price
(`price_min`/`price_max`), so write attribute values the way you'd want
them matched ("hybrid", "38 mpg", "AWD").

Missing fields are simply omitted from what the model sees, so she never
reads out a value that doesn't exist. Legacy cigar columns (wrapper,
strength, tasting notes, the recommendation columns) remain recognized for
the current store.

### Live price/stock overlay (optional)

`agent/catalog.py` can overlay live price and availability on top of the
static catalog at `get_product` time. The built-in connector is Shopify's
Storefront API, activated by env vars (`SHOPIFY_STORE_DOMAIN`,
`SHOPIFY_STOREFRONT_TOKEN`). Without them — or for a platform with no
connector yet — every price is flagged `price_stale` and she hedges to
"let me confirm at the register", which is always safe. To support another
platform live, implement an equivalent of `_shopify_overlay()` returning
`{price, in_stock, price_stale}`.

## What stays the same everywhere

- `docs/PROTOCOL.md` — the Unreal wire protocol, including the
  picture-in-picture `show_web_page` overlay with its ✕ button.
- `agent/engine.md` — generic behavior.
- The voice/vision stack, the server, the tools.
