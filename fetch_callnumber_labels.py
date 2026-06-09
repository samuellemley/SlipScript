#!/usr/bin/env python3
"""
fetch_callnumber_labels.py

1. Reads barcodes from a text file (one per line, lines starting with # are
   treated as comments).
2. Looks up each barcode in the CMU Libraries Alma catalog via the ExLibris
   Alma REST API (North America cluster).
3. Populates books.csv with the 4 columns expected by make_callnumber_strips.py:
       author, short_title, location, call_number
4. Runs make_callnumber_strips.py to generate a print-ready label-strips PDF.

Usage
-----
    python fetch_callnumber_labels.py barcodes.txt \\
        --api-key  YOUR_KEY \\
        --csv      books.csv \\
        --pdf      callnumber_strips.pdf

    # To skip PDF generation (just write the CSV):
    python fetch_callnumber_labels.py barcodes.txt --api-key KEY --no-pdf

    # Store the API key in an env var to keep it out of your shell history:
    export ALMA_API_KEY=your_key
    python fetch_callnumber_labels.py barcodes.txt

Authentication
--------------
The Alma API key is passed as the header  Authorization: apikey <KEY>.
To obtain a key, visit the ExLibris Developer Network:
    https://developers.exlibrisgroup.com/
and create a key with at minimum Read access to the Bibs and Items APIs.

North America base URL (CMU Libraries):
    https://api-na.hosted.exlibrisgroup.com
"""

from __future__ import annotations

import argparse
import copy
import csv
import os
import random
import re
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit(
        "The 'requests' library is required.\n"
        "Install it with:  pip install requests"
    )

# ── Constants ─────────────────────────────────────────────────────────────────

BASE_URL = "https://api-na.hosted.exlibrisgroup.com"
API_PATH = "/almaws/v1/items"

# ExLibris enforces a rate limit; 0.1 s between calls keeps well under it.
REQUEST_DELAY_SECONDS = 0.1

CSV_HEADERS = ["author", "short_title", "location", "call_number", "volume", "copy_number", "barcode"]

# ── Generated-barcode settings ─────────────────────────────────────────────────
# Prefix for app-generated barcodes.  Chosen to be structurally distinct from
# any pre-printed physical barcode sheets (which use the 38482018 prefix).
GENERATED_PREFIX = "38482099"   # 8 digits → 5-digit body + 1 Luhn check = 14 total

# Leading articles to strip when shortening a title
_ARTICLES = {"a", "an", "the"}

# Subtitle separators used in cataloging practice
_SUBTITLE_SEPS = re.compile(r"\s*[:/]\s+|\s+--\s+")


# ── Title shortening ──────────────────────────────────────────────────────────

def shorten_title(title: str, max_words: int = 5) -> str:
    """
    Return a concise, label-friendly version of a catalog title.

    Strategy (in order):
      1. Strip trailing punctuation artefacts (trailing period, comma, slash).
      2. Keep only the main title — drop anything after the first subtitle
         separator ( ' : ', ' / ', or ' -- ').
      3. Remove a leading article (A / An / The).
      4. Trim to at most *max_words* words.

    The default of 5 words fits comfortably on a narrow call-number strip.
    """
    if not title:
        return ""

    # Step 1 — strip trailing punctuation that catalogers often append
    title = title.strip().rstrip(".,/\\")

    # Step 2 — drop subtitle
    main = _SUBTITLE_SEPS.split(title, maxsplit=1)[0].strip()

    # Fall back to full title if splitting left nothing
    if not main:
        main = title

    words = main.split()

    # Step 3 — drop leading article
    if words and words[0].lower() in _ARTICLES:
        words = words[1:]

    # Step 4 — cap word count
    words = words[:max_words]

    return string.capwords(" ".join(words))


# ── Author formatting ─────────────────────────────────────────────────────────

def format_author(raw: str) -> str:
    """
    Return a short, label-friendly author string.

    Alma typically returns authors as  'Last, First M.'  or  'Last, First'.
    We return just the last name to keep the strip compact.
    If the record has no comma (e.g. a corporate author), we return the first
    word of the name (usually the organisation's key word).

    Examples:
        'García Márquez, Gabriel'  →  'García Márquez'
        'United States. Department of State'  →  'United States'
    """
    if not raw:
        return ""
    raw = raw.strip().rstrip(",.")
    if "," in raw:
        return raw.split(",")[0].strip()
    # Corporate/geographic authors use  'Name. Sub-unit'  (period separator)
    # Return everything before the first ' . ' or trailing period-space artefact
    if ". " in raw:
        return raw.split(". ")[0].strip()
    return raw.strip()


# ── Barcode generation ────────────────────────────────────────────────────────

def luhn_check(body: str) -> str:
    """Return the single Luhn mod-10 check digit for a string of digits.

    Double every digit at an odd position counting from the right (1-indexed),
    reduce any result > 9 by subtracting 9, then sum everything.  The check
    digit is (10 - sum % 10) % 10.
    """
    total = 0
    for i, d in enumerate(reversed([int(c) for c in body])):
        if i % 2 == 0:          # even 0-index from right = odd 1-index from right
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - (total % 10)) % 10)


def generate_barcode(
    api_key: str,
    base_url: str = BASE_URL,
    prefix: str = GENERATED_PREFIX,
    max_attempts: int = 200,
) -> Optional[str]:
    """Return a 14-digit barcode that does not yet exist in Alma.

    Generates candidates as:  prefix (8 digits)
                             + random 5-digit body
                             + Luhn check digit
    Each candidate is verified against the Alma Items API before being
    returned.  Returns None if no unique barcode is found within max_attempts.
    """
    for _ in range(max_attempts):
        body      = prefix + str(random.randint(0, 99_999)).zfill(5)  # 13 digits
        candidate = body + luhn_check(body)                            # 14 digits
        item      = fetch_item(candidate, api_key, base_url)
        if item is None:        # 404 → barcode not yet in use
            return candidate
    return None


# ── Alma API ──────────────────────────────────────────────────────────────────

def fetch_item(barcode: str, api_key: str, base_url: str = BASE_URL) -> Optional[dict]:
    """
    Call the Alma Items API by barcode.

    Returns the parsed JSON on success, None on recoverable errors
    (barcode not found, unexpected HTTP status).  Exits on fatal errors
    (bad API key, network timeout after retries).
    """
    url = base_url.rstrip("/") + API_PATH
    headers = {
        "Authorization": f"apikey {api_key}",
        "Accept": "application/json",
    }
    params = {
        "item_barcode": barcode,
        "format": "json",
    }

    for attempt in range(1, 4):  # up to 3 attempts
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=20)
        except requests.Timeout:
            if attempt == 3:
                print(f"    Timed out after 3 attempts — skipping.", file=sys.stderr)
                return None
            time.sleep(2 ** attempt)
            continue
        except requests.RequestException as exc:
            print(f"    Network error: {exc}", file=sys.stderr)
            return None

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 400:
            # Alma returns 400 for "item not found"
            errcode = ""
            try:
                errcode = resp.json()["errorList"]["error"][0]["errorCode"]
            except Exception:
                pass
            print(
                f"    Not found (HTTP 400{', code ' + errcode if errcode else ''})",
                file=sys.stderr,
            )
            return None

        if resp.status_code == 401:
            sys.exit(
                "\nFATAL: Alma returned HTTP 401 Unauthorized.\n"
                "Check that your API key is correct and has Read access to "
                "the Bibs and Items APIs.\n"
            )

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            print(f"    Rate-limited — waiting {wait}s …", file=sys.stderr)
            time.sleep(wait)
            continue

        print(
            f"    Unexpected HTTP {resp.status_code}: {resp.text[:120]}",
            file=sys.stderr,
        )
        return None

    return None


def update_item_barcode(
    item: dict,
    new_barcode: str,
    api_key: str,
    base_url: str = BASE_URL,
) -> Optional[dict]:
    """
    Write *new_barcode* onto an existing Alma item record.

    Takes the item JSON returned by ``fetch_item`` (which carries the
    ``mms_id``, ``holding_id`` and item ``pid`` we need), deep-copies it so
    the caller's dict is not mutated, swaps ``item_data.barcode`` for the
    new value, and PUTs the full record back to Alma.

    Endpoint:
        PUT /almaws/v1/bibs/{mms_id}/holdings/{holding_id}/items/{item_pid}

    The API key must have **Read/Write** access to the Bibs API.

    Returns
    -------
    dict
        The updated item JSON returned by Alma on HTTP 200.
    None
        On any recoverable failure (missing IDs in the source record,
        network error, non-200/2xx response).  Exits the process on a
        fatal 401 Unauthorized — same convention as ``fetch_item``.
    """
    # ── Pull the three identifiers we need out of the source record ───────────
    try:
        mms_id     = item["bib_data"]["mms_id"]
        holding_id = item["holding_data"]["holding_id"]
        item_pid   = item["item_data"]["pid"]
    except (KeyError, TypeError) as exc:
        print(
            f"    Cannot update item — missing identifier in record ({exc}).",
            file=sys.stderr,
        )
        return None

    # ── Build the payload ─────────────────────────────────────────────────────
    # PUT replaces the record wholesale, so we send back what we got with the
    # single field swapped.  Deep-copy so the caller still sees the original
    # (pre-update) item dict.
    payload = copy.deepcopy(item)
    payload.setdefault("item_data", {})["barcode"] = new_barcode

    url = (
        base_url.rstrip("/")
        + f"/almaws/v1/bibs/{mms_id}/holdings/{holding_id}/items/{item_pid}"
    )
    headers = {
        "Authorization": f"apikey {api_key}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }

    for attempt in range(1, 4):  # up to 3 attempts, same pattern as fetch_item
        try:
            resp = requests.put(url, headers=headers, json=payload, timeout=20)
        except requests.Timeout:
            if attempt == 3:
                print(
                    "    PUT timed out after 3 attempts — barcode NOT updated.",
                    file=sys.stderr,
                )
                return None
            time.sleep(2 ** attempt)
            continue
        except requests.RequestException as exc:
            print(f"    Network error during PUT: {exc}", file=sys.stderr)
            return None

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 401:
            sys.exit(
                "\nFATAL: Alma returned HTTP 401 Unauthorized on item PUT.\n"
                "Check that your API key is correct and has Read/Write access "
                "to the Bibs API.\n"
            )

        if resp.status_code == 403:
            print(
                "    PUT forbidden (HTTP 403) — API key lacks write permission "
                "on the Bibs API.",
                file=sys.stderr,
            )
            return None

        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 5))
            print(f"    Rate-limited on PUT — waiting {wait}s …", file=sys.stderr)
            time.sleep(wait)
            continue

        # 400 / 409 / 5xx — surface Alma's error body so the caller can debug
        snippet = (resp.text or "")[:300]
        print(
            f"    Unexpected HTTP {resp.status_code} on PUT: {snippet}",
            file=sys.stderr,
        )
        return None

    return None


def extract_row(item: dict) -> dict:
    """
    Pull the CSV fields out of an Alma item response object.
    """
    bib     = item.get("bib_data",     {})
    holding = item.get("holding_data", {})

    # ── author ──
    raw_author = (
        bib.get("author")
        or bib.get("creator")
        or ""
    )
    author = format_author(raw_author)

    # ── short_title ──
    short_title = shorten_title(bib.get("title", ""))

    # ── location ──
    # Location lives in item_data, not holding_data.
    # item_data.location.desc is often just the code (e.g. "POSNER-S"),
    # so prefer item_data.library.desc ("Posner Center") as the human-readable
    # label; fall back to the location value if library desc is absent.
    item_data = item.get("item_data", {})
    loc_val   = item_data.get("location", {}).get("value", "").strip()
    loc_desc  = item_data.get("location", {}).get("desc",  "").strip()
    lib_val   = item_data.get("library",  {}).get("value", "").strip()
    location  = loc_val or loc_desc or lib_val

    # ── call_number ──
    # Prefer the holding-level call number; fall back to the item-level one
    # (used when the item has been given an individual call number override).
    call_number = (
        holding.get("call_number")
        or item.get("item_data", {}).get("alternative_call_number")
        or ""
    ).strip()

    # ── volume ──
    # enumeration_a holds the volume/part designation for multi-volume sets
    # (e.g. "1", "v.1").  Fall back to the free-text description field.
    volume = (
        item_data.get("enumeration_a", "").strip()
        or item_data.get("description", "").strip()
    )
    if volume and volume.isdigit():
        volume = f"VOL. {volume}"

    # ── copy_number ──
    # Copy designation lives in holding_data.copy_id on CMU's Alma — this
    # is the "Copy ID" field in the holdings editor, which the item editor
    # also surfaces (inherited from the parent holding).  Confirmed
    # 2026-05-12 by inspecting raw JSON: item_data has no copy_id key,
    # holding_data has a populated one.
    #
    # Bare numerics get a "COPY NO. " prefix at print time; values that
    # already contain letters (e.g. "Copy 2", "c.1A") pass through.
    copy_number = (holding.get("copy_id") or "").strip()
    if copy_number.isdigit():
        n = int(copy_number)
        copy_number = f"COPY NO. {n}" if n > 1 else ""

    return {
        "author":      author,
        "short_title": short_title,
        "location":    location,
        "call_number": call_number,
        "volume":      volume,
        "copy_number": copy_number,
    }


# ── Barcode file reader ───────────────────────────────────────────────────────

def read_barcodes(path: Path) -> list[str]:
    """
    Read barcodes from a plain-text file.
    - One barcode per line.
    - Lines beginning with # are comments.
    - Blank lines are ignored.
    """
    barcodes = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            barcodes.append(line)
    return barcodes


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Fetch Alma catalog metadata for a list of barcodes, write "
            "books.csv, then generate a call-number label strips PDF."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    ap.add_argument(
        "barcodes_file",
        type=Path,
        help="Text file with one item barcode per line (# lines = comments).",
    )
    ap.add_argument(
        "--api-key",
        metavar="KEY",
        default=os.environ.get("ALMA_API_KEY"),
        help=(
            "Alma API key.  Alternatively, set the ALMA_API_KEY environment "
            "variable to keep the key out of your shell history."
        ),
    )
    ap.add_argument(
        "--csv",
        type=Path,
        default=Path("books.csv"),
        metavar="PATH",
        help="Output CSV path (default: books.csv).",
    )
    ap.add_argument(
        "--pdf",
        type=Path,
        default=Path("callnumber_strips.pdf"),
        metavar="PATH",
        help="Output PDF path (default: callnumber_strips.pdf).",
    )
    ap.add_argument(
        "--strips-script",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to make_callnumber_strips.py.  Defaults to the same "
            "directory as this script."
        ),
    )
    ap.add_argument(
        "--no-pdf",
        action="store_true",
        help="Write the CSV but skip PDF generation.",
    )
    ap.add_argument(
        "--max-title-words",
        type=int,
        default=5,
        metavar="N",
        help="Maximum words to keep in the shortened title (default: 5).",
    )
    ap.add_argument(
        "--strips-per-page",
        type=int,
        default=4,
        metavar="N",
        help="Strips per page passed to make_callnumber_strips.py (default: 4).",
    )

    args = ap.parse_args()

    # ── Validate ───────────────────────────────────────────────────────────────
    if not args.api_key:
        ap.error(
            "No API key provided.  Use --api-key KEY or set the "
            "ALMA_API_KEY environment variable."
        )

    if not args.barcodes_file.exists():
        ap.error(f"Barcodes file not found: {args.barcodes_file}")

    barcodes = read_barcodes(args.barcodes_file)
    if not barcodes:
        ap.error("Barcodes file is empty (no non-comment lines found).")

    # Deduplicate while preserving order
    seen = set()
    unique_barcodes = []
    for b in barcodes:
        if b not in seen:
            seen.add(b)
            unique_barcodes.append(b)
    if len(unique_barcodes) < len(barcodes):
        print(f"Note: removed {len(barcodes) - len(unique_barcodes)} duplicate barcode(s).")
    barcodes = unique_barcodes

    # ── Fetch ──────────────────────────────────────────────────────────────────
    print(f"Fetching metadata for {len(barcodes)} barcode(s) from Alma …\n")

    rows: list[dict] = []
    skipped = 0

    for idx, barcode in enumerate(barcodes, start=1):
        print(f"  [{idx:>3}/{len(barcodes)}]  {barcode}", end="  →  ", flush=True)

        item = fetch_item(barcode, args.api_key)
        if item is None:
            print("SKIPPED")
            skipped += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        row = extract_row(item)

        # Apply configurable max title words (override default inside extract_row)
        row["short_title"] = shorten_title(
            item.get("bib_data", {}).get("title", ""),
            max_words=args.max_title_words,
        )

        if not row["call_number"]:
            print("SKIPPED  (no call number in record)")
            skipped += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            continue

        row["barcode"] = barcode
        rows.append(row)
        print(f"{row['short_title']!r}  [{row['location']}]  {row['call_number']}")
        time.sleep(REQUEST_DELAY_SECONDS)

    print(
        f"\nFetched: {len(rows)}   Skipped: {skipped}   "
        f"Total barcodes: {len(barcodes)}"
    )

    if not rows:
        sys.exit("\nNo records retrieved — books.csv not written.")

    # ── Write CSV ──────────────────────────────────────────────────────────────
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} row(s)  →  {args.csv.resolve()}")

    if args.no_pdf:
        print("--no-pdf set; skipping label generation.")
        return

    # ── Generate PDF ───────────────────────────────────────────────────────────
    if args.strips_script is None:
        # Default: same directory as this script
        args.strips_script = Path(__file__).parent / "make_callnumber_strips.py"

    if not args.strips_script.exists():
        print(
            f"\n[WARN] make_callnumber_strips.py not found at "
            f"{args.strips_script}.\n"
            "Skipping PDF generation.  Use --strips-script to specify the path.",
            file=sys.stderr,
        )
        return

    cmd = [
        sys.executable,
        str(args.strips_script),
        str(args.csv),
        "-o", str(args.pdf),
        "--strips-per-page", str(args.strips_per_page),
    ]

    print(f"\nGenerating PDF …\n  {' '.join(str(c) for c in cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        sys.exit(f"\nPDF generation failed (exit code {result.returncode}).")

    print(f"\nDone!  Label strips PDF  →  {args.pdf.resolve()}")


if __name__ == "__main__":
    main()
