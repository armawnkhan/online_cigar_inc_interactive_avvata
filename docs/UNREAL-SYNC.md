# Pending sync → Cigar-Inc-Interactive-Avator (Unreal repo)

These files are byte-identical copies shared with the Unreal repo. There is no
automatic sync. Everything below changed HERE and has NOT yet been applied there.

Per Arman (2026-08-14): batch these later, don't interrupt the web work for them.

| Date | File | Change | How to apply there |
|---|---|---|---|
| 2026-08-14 | `data/catalog.db` | Rebuilt — old DB was missing `product_extra.key_norm` AND the `extra` column on `products_fts`, so attribute filters silently fell back to a title-only LIKE search | Re-run the build command below |
| 2026-08-14 | `data/house_rules.md` | Added rule under "Always do this": a sampler is a cigar product, never an accessory | Copy the rule text across |
| 2026-08-14 | `data/products.xlsx` | Replaced: 50 rows → all 1,013 store products, with wrapper/origin/strength/tasting_notes/occasion/palate filled | Copy the file across |
| 2026-08-14 | `scripts/build_catalog.py` | Derives `smoke_minutes` (tobacco volume, 1 mL = 1 min) and `time_of_day` (from strength 1–5) when the sheet leaves them blank | Copy the file across |
| 2026-08-19 | `agent/brain.py` | `greet()` names a random opener number so the greeting actually varies between sessions | Copy the greet() change |
| 2026-08-14 | `agent/catalog.py` | Added `smoke_minutes` to `FIELDS`, and `smoke_minutes` + `time_of_day` to `BRIEF_FIELDS` so the model can see them | Copy the two list edits |

## Rebuild command (run in the Unreal repo)

```
python scripts/build_catalog.py data/products.xlsx
```

## Not yet applied anywhere — open bug

`agent/brain.py:27` — `SENTENCE_END` splits on the period in abbreviations, so
"Cigar Inc." breaks mid-sentence and ships an orphan fragment to TTS. Affects
both bodies identically. Not fixed yet.
