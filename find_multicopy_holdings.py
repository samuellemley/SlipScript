#!/usr/bin/env python3
"""
find_multicopy_holdings.py

Walk an Alma physical-items set, group its members by (bib mms_id,
holding_id), and report every holding that contains more than one item
— i.e., titles held in multiple copies.

Outputs a CSV with one row per multi-copy holding:
    mms_id, title, author, call_number, location, holding_id,
    copy_count, barcodes

Usage:
    export ALMA_API_KEY=...
    python3 find_multicopy_holdings.py 13294185950004436

    # write to a specific path:
    python3 find_multicopy_holdings.py 13294185950004436 --out report.csv

    # just count, don't enrich (fast — useful to gauge scope before the
    # slow per-item fetch phase):
    python3 find_multicopy_holdings.py 13294185950004436 --count-only
"""
import argparse
import csv
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

from fetch_callnumber_labels import REQUEST_DELAY_SECONDS


def fetch_set_members(set_id: str, api_key: str, base_url: str) -> list[dict]:
    """Iterate the Sets API with pagination and return every member dict."""
    members: list[dict] = []
    offset = 0
    limit = 100
    while True:
        url = base_url.rstrip("/") + f"/almaws/v1/conf/sets/{set_id}/members"
        params = {"limit": limit, "offset": offset}
        headers = {"Authorization": f"apikey {api_key}",
                   "Accept": "application/json"}
        for attempt in range(1, 4):
            try:
                resp = requests.get(url, headers=headers, params=params,
                                    timeout=30)
                break
            except requests.RequestException as exc:
                if attempt == 3:
                    print(f"  set members GET failed after 3 attempts: {exc}",
                          file=sys.stderr)
                    return members
                time.sleep(2 ** attempt)
        if resp.status_code != 200:
            print(f"  set members GET HTTP {resp.status_code}: "
                  f"{resp.text[:300]}", file=sys.stderr)
            return members

        data = resp.json()
        page = data.get("member", []) or []
        members.extend(page)
        total = data.get("total_record_count", 0)
        print(f"  fetched {len(members)}/{total} members", file=sys.stderr)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(REQUEST_DELAY_SECONDS)
    return members


_LINK_RE = re.compile(r"/bibs/(\d+)/holdings/(\d+)/items/(\d+)")

def parse_item_link(link: str | None) -> tuple[str, str, str] | None:
    """Return (mms_id, holding_id, item_pid) from a member 'link' URL."""
    if not link:
        return None
    m = _LINK_RE.search(link)
    if not m:
        return None
    return (m.group(1), m.group(2), m.group(3))


def fetch_item_by_path(mms_id: str, holding_id: str, item_pid: str,
                       api_key: str, base_url: str) -> dict | None:
    url = (base_url.rstrip("/")
           + f"/almaws/v1/bibs/{mms_id}/holdings/{holding_id}"
             f"/items/{item_pid}")
    headers = {"Authorization": f"apikey {api_key}",
               "Accept": "application/json"}
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429,):
            wait = int(resp.headers.get("Retry-After", 5))
            time.sleep(wait)
            continue
        return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("set_id", help="Alma set ID (logical or itemized)")
    ap.add_argument("--out", type=Path, default=Path("multicopy_holdings.csv"),
                    help="Output CSV path (default: multicopy_holdings.csv)")
    ap.add_argument("--count-only", action="store_true",
                    help="Skip the slow per-item enrichment phase; just "
                         "count multi-copy holdings and exit.")
    args = ap.parse_args()

    api_key  = os.environ.get("ALMA_API_KEY", "")
    base_url = os.environ.get("ALMA_BASE_URL",
                              "https://api-na.hosted.exlibrisgroup.com")
    if not api_key:
        sys.exit("Set ALMA_API_KEY in your environment first.")

    print(f"── Step 1: walk set {args.set_id} ──", file=sys.stderr)
    members = fetch_set_members(args.set_id, api_key, base_url)
    if not members:
        sys.exit("No members returned — check the set_id and API key.")

    # Group item PIDs by (mms_id, holding_id)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    skipped = 0
    for m in members:
        ids = parse_item_link(m.get("link"))
        if ids is None:
            skipped += 1
            continue
        mms_id, holding_id, item_pid = ids
        groups[(mms_id, holding_id)].append(item_pid)

    multi = {k: v for k, v in groups.items() if len(v) > 1}
    total_multi_items = sum(len(v) for v in multi.values())

    print(f"\n── Step 2: group ──", file=sys.stderr)
    print(f"  total members           : {len(members)}", file=sys.stderr)
    print(f"  unparseable links       : {skipped}", file=sys.stderr)
    print(f"  unique (bib, holding)   : {len(groups)}", file=sys.stderr)
    print(f"  multi-copy holdings     : {len(multi)}", file=sys.stderr)
    print(f"  items in multi-copy     : {total_multi_items}", file=sys.stderr)

    if args.count_only:
        return

    print(f"\n── Step 3: enrich {len(multi)} multi-copy holdings "
          f"({total_multi_items} fetches) ──", file=sys.stderr)
    rows = []
    fetched = 0
    for (mms_id, holding_id), pids in multi.items():
        title = author = call_number = location = ""
        barcodes: list[str] = []
        for pid in pids:
            it = fetch_item_by_path(mms_id, holding_id, pid, api_key, base_url)
            fetched += 1
            time.sleep(REQUEST_DELAY_SECONDS)
            if it is None:
                continue
            if not title:
                bib   = it.get("bib_data", {})
                hold  = it.get("holding_data", {})
                idata = it.get("item_data", {})
                title       = bib.get("title", "")
                author      = bib.get("author", "")
                call_number = hold.get("call_number", "")
                location    = idata.get("location", {}).get("value", "")
            bc = it.get("item_data", {}).get("barcode", "")
            if bc:
                barcodes.append(bc)
            if fetched % 50 == 0:
                print(f"  fetched {fetched}/{total_multi_items}",
                      file=sys.stderr)

        rows.append({
            "mms_id":      mms_id,
            "title":       title,
            "author":      author,
            "call_number": call_number,
            "location":    location,
            "holding_id":  holding_id,
            "copy_count":  len(pids),
            "barcodes":    "; ".join(barcodes),
        })

    rows.sort(key=lambda r: (r["location"], r["call_number"], r["title"]))

    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "mms_id", "title", "author", "call_number", "location",
            "holding_id", "copy_count", "barcodes",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n── done — wrote {len(rows)} multi-copy holdings to "
          f"{args.out.resolve()} ──", file=sys.stderr)


if __name__ == "__main__":
    main()
