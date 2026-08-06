#!/usr/bin/env python3
"""Generate the three Redd Shopping TSVs (D-070 workstream A).

Template = the archived Feedzly row for that product. Titles, descriptions,
categories, links, images, labels, gtin, mpn all stay EXACTLY as archived,
because that content is what is approved and serving in Merchant Center today.
The smaller the diff, the smaller the disapproval risk.

Overlaid from the live Shopify Admin API, and nothing else:
  - price          (variant price, currency token carried over from the archive)
  - availability   (variant availableForSale -> in_stock / out_of_stock)

Row order, column order and byte shape match the archive, including the missing
trailing newline.

Read only against Shopify. Writes only to out/.
Creates no Merchant Center data source and changes no Merchant Center setting.

Usage:
  python gen-feeds.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation

import feedlib as fl

QUERY = """
query($ids:[ID!]!){
  nodes(ids:$ids){
    __typename
    ... on ProductVariant {
      id price availableForSale
      product { status }
    }
  }
}
"""

def money(raw: str, archived_price: str) -> str:
    """Shopify returns a bare decimal string. Google wants '<amount> <currency>'.
    Currency token is carried over from the archived row so we never invent one."""
    parts = archived_price.split()
    currency = parts[1] if len(parts) > 1 else "USD"
    try:
        return f"{Decimal(raw):.2f} {currency}"
    except (InvalidOperation, TypeError):
        raise SystemExit(f"FATAL: Shopify returned an unparseable price: {raw!r}")


def main() -> int:
    if not fl.MAPPING_PATH.exists():
        raise SystemExit("FATAL: mapping.json missing. Run build-mapping.py first.")
    mapping = json.loads(fl.MAPPING_PATH.read_text(encoding="utf-8"))
    if mapping["unresolved"] or mapping["counts"]["total"] != 63:
        raise SystemExit("FATAL: mapping.json has unresolved rows. Fix the mapping before generating.")
    by_id = {r["id"]: r for r in mapping["rows"]}

    variant_ids = sorted({r["shopify_variant_id"] for r in mapping["rows"]})
    data = fl.shopify_gql(QUERY, {"ids": [f"gid://shopify/ProductVariant/{v}" for v in variant_ids]})
    live = {}
    for node in data["nodes"]:
        if node and node.get("__typename") == "ProductVariant":
            live[node["id"].rsplit("/", 1)[-1]] = node
    gone = [v for v in variant_ids if v not in live]
    if gone:
        raise SystemExit(
            "FATAL: these mapped variants no longer resolve on Shopify, so a generated "
            f"feed would carry stale rows: {gone}. Re-run build-mapping.py and investigate."
        )

    total_price, total_avail = 0, 0
    for label, _source, filename, expected in fl.FEEDS:
        header, rows = fl.read_archive(filename)
        # Column ORDER is per feed: FDZ-URIC swaps custom_label_0 and _1.
        I_ID, I_AVAIL, I_PRICE = (header.index(c) for c in ("id", "availability", "price"))
        out_rows, price_moves, avail_moves = [], [], []
        for r in rows:
            item_id = r[I_ID]
            m = by_id.get(item_id)
            if m is None or m["feed"] != label:
                raise SystemExit(f"FATAL: {item_id} in {filename} is not in mapping.json under {label}.")
            v = live[m["shopify_variant_id"]]
            row = list(r)
            row[I_PRICE] = money(v["price"], r[I_PRICE])
            row[I_AVAIL] = "in_stock" if v["availableForSale"] else "out_of_stock"
            if row[I_PRICE] != r[I_PRICE]:
                price_moves.append(f"{item_id}: {r[I_PRICE]} -> {row[I_PRICE]}")
            if row[I_AVAIL] != r[I_AVAIL]:
                avail_moves.append(f"{item_id}: {r[I_AVAIL]} -> {row[I_AVAIL]}")
            out_rows.append(row)

        if len(out_rows) != expected:
            raise SystemExit(f"FATAL: {filename} produced {len(out_rows)} rows, expected {expected}.")

        path = fl.OUT_DIR / filename
        fl.write_tsv(path, header, out_rows)
        total_price += len(price_moves)
        total_avail += len(avail_moves)
        print(f"  {label:<8} {len(out_rows):>2} rows -> {path.name}  "
              f"price drift {len(price_moves)}, availability drift {len(avail_moves)}")
        for line in price_moves + avail_moves:
            print(f"           {line}")

    print(f"\nPASS: 63 rows written to {fl.OUT_DIR} "
          f"(vs the 8/4 archive: {total_price} price change(s), {total_avail} availability change(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
