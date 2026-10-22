#!/usr/bin/env python3
"""Shared plumbing for the Redd Shopping feed generator (D-070 workstream A).

Three scripts use this: build-mapping.py, gen-feeds.py, shadow-diff.py.
Nothing here talks to Merchant Center. Nothing here writes to Shopify.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE  # the scripts live at the root of this dedicated repo
ARCHIVE_DIR = REPO_ROOT / "templates"  # the 2026-08-04 blueprint TSVs + sources.txt
OUT_DIR = HERE / "out"
MAPPING_PATH = HERE / "mapping.json"

STORE = "redd-remedies.myshopify.com"
API_VERSION = "2026-01"
TOKEN_ENV = "SHOPIFY_ADMIN_TOKEN_REDD"

# (Merchant Center feed label, source name, archived file, expected product count).
# Feed labels are exact and load bearing: a label cannot be edited after a data
# source is created, only deleted and recreated. See the 8/3 readiness doc.
FEEDS = [
    ("SMPL", "FDZ-SMPL", "FDZ-SMPL.tsv", 19),
    ("BRND", "FDZ-BRND", "FDZ-BRND.tsv", 40),
    ("FDZ-URC", "FDZ-URIC", "FDZ-URIC.tsv", 4),
]

# The archived files are plain tab separated with no quoting, no embedded tabs
# or newlines, and NO trailing newline. Anything we emit matches that byte shape.
#
# COLUMNS is the canonical 24 column set, in the order FDZ-SMPL and FDZ-BRND use.
# FDZ-URIC ships the SAME 24 columns with custom_label_1 and custom_label_0
# swapped (real, verified in the 8/4 archive). So column ORDER is per feed: read
# each file's own header and reproduce it, never assume one global order.
COLUMNS = [
    "id", "title", "description", "link", "image_link", "mobile_link",
    "additional_image_link", "availability", "price", "google_product_category",
    "product_type", "brand", "gtin", "mpn", "condition", "age_group", "color",
    "gender", "promotion_id", "custom_label_0", "custom_label_1",
    "custom_label_2", "custom_label_3", "custom_label_4",
]


def load_env() -> None:
    """Overlay repo-root .env into os.environ (CI supplies real env vars)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


def shopify_token() -> str:
    load_env()
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise SystemExit(
            f"FATAL: {TOKEN_ENV} is not set (checked os.environ and {REPO_ROOT / '.env'}). "
            "Nothing was generated. Fix the token, then re-run."
        )
    return token


def shopify_gql(query: str, variables: dict | None = None) -> dict:
    """One POST to the Shopify Admin GraphQL API. Raises on any error."""
    r = requests.post(
        f"https://{STORE}/admin/api/{API_VERSION}/graphql.json",
        json={"query": query, "variables": variables or {}},
        headers={"X-Shopify-Access-Token": shopify_token(),
                 "Content-Type": "application/json"},
        timeout=60,
    )
    if r.status_code != 200:
        raise SystemExit(
            f"FATAL: Shopify Admin API returned HTTP {r.status_code} for {STORE} "
            f"(api {API_VERSION}). Body: {r.text[:500]}"
        )
    payload = r.json()
    if payload.get("errors"):
        raise SystemExit(f"FATAL: Shopify GraphQL errors: {payload['errors']}")
    return payload["data"]


def parse_tsv(raw: str) -> tuple[list[str], list[list[str]]]:
    """Split a Feedzly style TSV into (header, rows). Tolerates CRLF and a
    trailing newline on the LIVE side; our own files have neither."""
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n")
    table = [ln.split("\t") for ln in lines if ln != ""]
    if not table:
        raise ValueError("empty TSV")
    header, rows = table[0], table[1:]
    bad = [n for n, r in enumerate(rows, start=2) if len(r) != len(header)]
    if bad:
        raise ValueError(f"column count mismatch on line(s) {bad[:5]}")
    return header, rows


def read_archive(filename: str) -> tuple[list[str], list[list[str]]]:
    """Header comes back in the file's OWN order (see the COLUMNS note)."""
    header, rows = parse_tsv((ARCHIVE_DIR / filename).read_text(encoding="utf-8"))
    if sorted(header) != sorted(COLUMNS):
        raise SystemExit(
            f"FATAL: archive {filename} does not carry the canonical 24 columns. "
            f"Got {len(header)}: {header}"
        )
    return header, rows


def write_tsv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write with LF endings and NO trailing newline, matching the archive."""
    body = "\n".join("\t".join(r) for r in [header] + rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="")


def read_sources() -> dict[str, str]:
    """sources.txt lines look like: FDZ-SMPL|https://app.feedzly.com/..."""
    out = {}
    for line in (ARCHIVE_DIR / "sources.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        name, url = line.split("|", 1)
        out[name.strip()] = url.strip()
    return out
