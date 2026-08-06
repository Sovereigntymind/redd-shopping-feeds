# redd-shopping-feeds

Three Google Shopping product feeds for Redd Remedies, regenerated daily from the
live Shopify catalog by a GitHub Actions cron and served as static files.

| Feed label | File | Products |
|---|---|---|
| `SMPL` | `out/FDZ-SMPL.tsv` | 19 |
| `BRND` | `out/FDZ-BRND.tsv` | 40 |
| `FDZ-URC` | `out/FDZ-URIC.tsv` | 4 |

Public raw URLs (what Merchant Center fetches):

```
https://raw.githubusercontent.com/Sovereigntymind/redd-shopping-feeds/main/out/FDZ-SMPL.tsv
https://raw.githubusercontent.com/Sovereigntymind/redd-shopping-feeds/main/out/FDZ-BRND.tsv
https://raw.githubusercontent.com/Sovereigntymind/redd-shopping-feeds/main/out/FDZ-URIC.tsv
```

## How it works

`templates/` holds the 2026-08-04 blueprint TSVs. Those rows are the template:
titles, descriptions, categories, links, images, gtin, mpn and custom labels stay
exactly as captured, because that content is what is approved and serving today.
Only two fields are overlaid from live Shopify: **price** and **availability**.
Row order, column order and byte shape match the blueprint, including the missing
trailing newline.

`mapping.json` is the frozen link from each `manifold_*` item ID to its Shopify
product and variant. Google keys product approval history and Smart Bidding signal
on the item ID, so those exact strings are load bearing and never change.

`out/*.tsv` is committed output. The workflow pushes it only when it changed.

## Run it

```bash
pip install -r requirements.txt
python gen-feeds.py            # writes out/*.tsv
python shadow-diff.py          # validation gate vs the live source feeds
python build-mapping.py --check   # verify the frozen mapping still resolves
python shadow-diff.py --selftest  # offline check of the differ itself
```

Needs `SHOPIFY_ADMIN_TOKEN_REDD` in the environment, or in a `.env` file at the
repo root for local runs. In Actions it comes from the repo secret of the same
name. Every script exits nonzero on any failure and refuses to write partial
output.

## The daily job

`.github/workflows/regen.yml` runs at 06:10 UTC daily plus on demand
(`workflow_dispatch`). Steps: generate, then shadow diff, then commit and push
`out/*.tsv` if anything changed.

`shadow-diff.py` is the gate. It fetches the live source feeds and diffs them
against what we generated the same day. Only `price`, `sale_price` and
`availability` are allowed to differ. A missing row, an extra row, a changed id,
title, link, image or category, or header drift fails the job red. Generation
failure fails the job red and blocks the commit.

## Notes worth not rediscovering

- `FDZ-URIC` swaps `custom_label_1` and `custom_label_0` relative to the other two
  feeds. Same 24 columns, different order. Column order is read per feed from the
  file's own header, never assumed globally.
- Availability comes from the variant's `availableForSale`, not from inventory
  count. Several variants sit at or below zero inventory with an oversell policy
  and are correctly in stock.
- Files carry LF endings and no trailing newline. `.gitattributes` pins that so a
  Windows checkout cannot change the bytes.
- Currency token is carried over from the blueprint row rather than hardcoded.

Nothing in this repo touches Merchant Center. It only produces files.
