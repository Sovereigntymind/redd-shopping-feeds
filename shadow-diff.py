#!/usr/bin/env python3
"""SHADOW MODE proof: our generated feed vs the LIVE Feedzly feed (D-070 workstream A).

Kevin 8/5: "stand him up in parallel until we need to transition." This is that
parallel run. It fetches the three live Feedzly files the same day we generate
ours and diffs them field by field. It runs entirely OUTSIDE Merchant Center. No
second data source is created until cutover day, because two live primary sources
carrying the same item IDs under the same feed label is the one configuration
Google's docs warn about.

Only three fields are allowed to differ, because only three are meant to be live:
  price, sale_price, availability

Anything else is a real regression: a missing row, an extra row, a changed id,
title, link, image, category, or header drift. Those print loudly and exit
nonzero, so a scheduled run cannot fail silently.

Read only. Fetches public URLs and local files. Writes nothing.

Usage:
  python shadow-diff.py
  python shadow-diff.py --selftest   # offline check of the differ
"""
from __future__ import annotations

import sys

import requests

import feedlib as fl

ALLOWED = {"price", "sale_price", "availability"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MAX_DETAIL = 25


def diff_feed(live: tuple[list[str], list[list[str]]],
              ours: tuple[list[str], list[list[str]]]) -> tuple[list[str], list[str], list[str]]:
    """Returns (problems, allowed_deltas, notes). Problems mean the run fails."""
    live_header, live_rows = live
    ours_header, ours_rows = ours
    problems: list[str] = []
    deltas: list[str] = []
    notes: list[str] = []

    if live_header != ours_header:
        only_live = [c for c in live_header if c not in ours_header]
        only_ours = [c for c in ours_header if c not in live_header]
        problems.append(f"HEADER DRIFT: live has {len(live_header)} cols, ours has {len(ours_header)}")
        if only_live:
            problems.append(f"  columns only in live: {only_live}")
        if only_ours:
            problems.append(f"  columns only in ours: {only_ours}")
        if live_header != ours_header and not only_live and not only_ours:
            problems.append(f"  column ORDER differs: live {live_header} vs ours {ours_header}")
        return problems, deltas, notes  # field diffs are meaningless once columns disagree

    idx = {c: n for n, c in enumerate(live_header)}
    id_col = idx["id"]
    live_by_id = {r[id_col]: r for r in live_rows}
    ours_by_id = {r[id_col]: r for r in ours_rows}

    for item_id in live_by_id.keys() - ours_by_id.keys():
        problems.append(f"MISSING ROW: {item_id} is in the live feed, not in ours")
    for item_id in ours_by_id.keys() - live_by_id.keys():
        problems.append(f"EXTRA ROW: {item_id} is in our feed, not in the live one")

    live_order = [r[id_col] for r in live_rows if r[id_col] in ours_by_id]
    ours_order = [r[id_col] for r in ours_rows if r[id_col] in live_by_id]
    if live_order != ours_order:
        notes.append("row ORDER differs from the live feed (not a failure, Google does not order match)")

    for item_id in live_order:
        lrow, orow = live_by_id[item_id], ours_by_id[item_id]
        for col, n in idx.items():
            if lrow[n] == orow[n]:
                continue
            line = f"{item_id}  {col}:  live [{lrow[n]}]  ours [{orow[n]}]"
            (deltas if col in ALLOWED else problems).append(
                line if col in ALLOWED else f"FIELD CHANGED (not allowed): {line}")
    return problems, deltas, notes


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=90)
    if r.status_code != 200:
        raise SystemExit(f"FATAL: live feed fetch returned HTTP {r.status_code} for {url}")
    r.encoding = r.encoding or "utf-8"
    return r.text


def selftest() -> int:
    header = list(fl.COLUMNS)
    base = ["x"] * len(header)
    base[header.index("id")] = "manifold_1_smpl"
    base[header.index("title")] = "Thing"
    base[header.index("price")] = "10.00 USD"
    base[header.index("availability")] = "in_stock"

    same = list(base)
    p, d, _ = diff_feed((header, [base]), (header, [same]))
    assert not p and not d, (p, d)

    priced = list(base)
    priced[header.index("price")] = "12.00 USD"
    priced[header.index("availability")] = "out_of_stock"
    p, d, _ = diff_feed((header, [base]), (header, [priced]))
    assert not p and len(d) == 2, (p, d)

    retitled = list(base)
    retitled[header.index("title")] = "Thing v2"
    p, d, _ = diff_feed((header, [base]), (header, [retitled]))
    assert len(p) == 1 and "title" in p[0], p

    p, d, _ = diff_feed((header, [base]), (header, []))
    assert len(p) == 1 and p[0].startswith("MISSING ROW"), p

    p, d, _ = diff_feed((header, []), (header, [base]))
    assert len(p) == 1 and p[0].startswith("EXTRA ROW"), p

    p, d, _ = diff_feed((header[:-1], [base[:-1]]), (header, [base]))
    assert p and p[0].startswith("HEADER DRIFT"), p

    print("selftest OK: differ allows price and availability, fails on title, row count, header")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    sources = fl.read_sources()
    failed = False
    summary = []

    for label, source, filename, expected in fl.FEEDS:
        our_path = fl.OUT_DIR / filename
        if not our_path.exists():
            raise SystemExit(f"FATAL: {our_path} missing. Run gen-feeds.py first.")
        url = sources.get(source)
        if not url:
            raise SystemExit(f"FATAL: no live URL for {source} in the archive sources.txt")

        live = fl.parse_tsv(fetch(url))
        ours = fl.parse_tsv(our_path.read_text(encoding="utf-8"))
        problems, deltas, notes = diff_feed(live, ours)

        verdict = "FAIL" if problems else "PASS"
        failed = failed or bool(problems)
        summary.append(
            f"  {label:<8} {source:<9} live {len(live[1]):>2} rows / ours {len(ours[1]):>2} rows "
            f"(expected {expected})  price+availability deltas {len(deltas):>2}  {verdict}")

        print(f"\n=== {label} ({source}) ===")
        print(f"  live: {url}")
        for note in notes:
            print(f"  note: {note}")
        for line in deltas[:MAX_DETAIL]:
            print(f"  delta: {line}")
        if len(deltas) > MAX_DETAIL:
            print(f"  delta: ... {len(deltas) - MAX_DETAIL} more allowed delta(s)")
        if problems:
            print("  !!! UNEXPECTED DIFFERENCES, this feed is NOT safe to cut over:")
            for line in problems[:MAX_DETAIL]:
                print(f"      {line}")
            if len(problems) > MAX_DETAIL:
                print(f"      ... {len(problems) - MAX_DETAIL} more problem(s)")
        else:
            print("  no unexpected differences")

    print("\nSHADOW MODE SUMMARY")
    for line in summary:
        print(line)
    if failed:
        print("\nRESULT: FAIL. At least one feed differs beyond price and availability. Do not cut over.")
        return 1
    print("\nRESULT: PASS. Every difference is price or availability. Parallel run is clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
