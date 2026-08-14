# data/ — the business definition

This folder defines the ENTIRE business the avatar works for. The engine
code is generic; edit these files, restart her, and she's a different store.
See docs/CONFIG.md for the full guide.

- `persona.md` — who she is: her name, the business, her character.
- `store_facts.md` — every true fact she may state. Nothing else gets stated.
- `house_rules.md` — operator rules, the subscriber offer + sign-up URL,
  hard-blocked words.
- `products.xlsx` — the catalog (git-ignored; copy or regenerate per
  machine). Build with `python scripts/build_catalog.py data/products.xlsx`.
