#!/usr/bin/env python3
"""Freeze the manifold_* item ID -> live Shopify variant mapping (D-070 workstream A).

Why this exists: Google keys product approval history and Smart Bidding signal on
the item ID. The three Feedzly feeds emit `manifold_*` IDs; a stock Shopify feed
emits `shopify_ZZ_*`, a different namespace. Emitting the wrong namespace resets
years of signal on the feeds carrying all of Redd's Shopping spend. So we keep the
vendor's exact ID strings and map each one to the Shopify variant it points at.

Candidates come from the archived TSV `link` column, which carries the product
handle in the path and the variant in the `?variant=` param. Every candidate is
then CONFIRMED against the Shopify Admin API: the variant must exist, its product
must be ACTIVE, and the product handle must match the handle in the link. Anything
that fails goes to `unresolved` and is reported loudly. Nothing is guessed.

Read only against Shopify. Touches no Merchant Center setting.

Usage:
  python build-mapping.py            # write mapping.json
  python build-mapping.py --check    # verify, write nothing
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import feedlib as fl

VARIANT_GID = "gid://shopify/ProductVariant/{}"

QUERY = """
query($ids:[ID!]!){
  nodes(ids:$ids){
    __typename
    ... on ProductVariant {
      id
      product { id handle status }
    }
  }
}
"""

# FDZ-URIC IDs embed both numeric IDs: manifold_<productId>_<variantId>_uricacid.
URIC_ID = re.compile(r"^manifold_(\d+)_(\d+)_uricacid$")


def candidate_from_link(link: str) -> tuple[str, str]:
    """(handle, variant_id) derived from the archived link column."""
    u = urlparse(link)
    handle = unquote(u.path.rsplit("/", 1)[-1])
    variant = parse_qs(u.query).get("variant", [""])[0]
    return handle, variant


def gid_num(gid: str) -> str:
    return gid.rsplit("/", 1)[-1]


def main() -> int:
    check_only = "--check" in sys.argv

    candidates = []
    for label, source, filename, expected in fl.FEEDS:
        header, rows = fl.read_archive(filename)
        if len(rows) != expected:
            raise SystemExit(
                f"FATAL: {filename} holds {len(rows)} rows, expected {expected}. "
                "The archive is the blueprint; do not proceed on a changed archive."
            )
        i_id, i_link = header.index("id"), header.index("link")
        for n, r in enumerate(rows):
            item_id = r[i_id]
            handle, variant = candidate_from_link(r[i_link])
            candidates.append({
                "id": item_id, "feed": label, "source": source,
                "row": n, "handle": handle, "variant_id": variant,
            })

    total = len(candidates)
    if total != 63:
        raise SystemExit(f"FATAL: {total} candidate rows, expected 63 (19 + 40 + 4).")

    missing_variant = [c["id"] for c in candidates if not c["variant_id"]]
    if missing_variant:
        print(f"NOTE: {len(missing_variant)} archived link(s) carry no ?variant= param")

    # One call resolves all 63. nodes() returns null for anything that is gone.
    gids = [VARIANT_GID.format(c["variant_id"]) for c in candidates if c["variant_id"]]
    data = fl.shopify_gql(QUERY, {"ids": gids})
    by_variant = {}
    for node in data["nodes"]:
        if node and node.get("__typename") == "ProductVariant":
            by_variant[gid_num(node["id"])] = node

    rows_out, unresolved = [], []
    for c in candidates:
        node = by_variant.get(c["variant_id"])
        if node is None:
            unresolved.append({**c, "reason": "variant not found on Shopify (deleted or wrong id)"})
            continue
        prod = node["product"]
        if prod["status"] != "ACTIVE":
            unresolved.append({**c, "reason": f"product status is {prod['status']}, not ACTIVE"})
            continue
        if prod["handle"] != c["handle"]:
            unresolved.append({**c, "reason": f"handle mismatch: link says {c['handle']}, Shopify says {prod['handle']}"})
            continue
        product_id = gid_num(prod["id"])
        m = URIC_ID.match(c["id"])
        if m and (m.group(1), m.group(2)) != (product_id, c["variant_id"]):
            unresolved.append({**c, "reason": f"uricacid item id embeds {m.group(1)}/{m.group(2)}, Shopify says {product_id}/{c['variant_id']}"})
            continue
        rows_out.append({
            "id": c["id"],
            "feed": c["feed"],
            "shopify_product_id": product_id,
            "shopify_variant_id": c["variant_id"],
            "handle": prod["handle"],
        })

    counts = {label: sum(1 for r in rows_out if r["feed"] == label) for label, _, _, _ in fl.FEEDS}
    for label, _, _, expected in fl.FEEDS:
        status = "ok" if counts[label] == expected else "SHORT"
        print(f"  {label:<8} resolved {counts[label]}/{expected}  {status}")
    print(f"  {'TOTAL':<8} resolved {len(rows_out)}/63")

    if unresolved:
        print("\n!!! UNRESOLVED, do not build a feed on this mapping:")
        for u in unresolved:
            print(f"    {u['feed']:<8} {u['id']}  handle={u['handle']} variant={u['variant_id']}")
            print(f"             reason: {u['reason']}")

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "store": fl.STORE,
        "api_version": fl.API_VERSION,
        "archive": "templates/ (2026-08-04 blueprint)",
        "counts": {**counts, "total": len(rows_out)},
        "unresolved": unresolved,
        "rows": rows_out,
    }

    if unresolved or len(rows_out) != 63:
        print("\nFAIL: mapping is incomplete. mapping.json NOT written.")
        return 1
    if check_only:
        existing = json.loads(fl.MAPPING_PATH.read_text(encoding="utf-8"))
        drift = [r for r in rows_out if r not in existing["rows"]]
        print("\nCHECK: mapping matches frozen file" if not drift
              else f"\nCHECK FAIL: {len(drift)} row(s) drifted from mapping.json")
        return 1 if drift else 0

    fl.MAPPING_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nPASS: 63/63 mapped, 0 unresolved. Wrote {fl.MAPPING_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
