#!/usr/bin/env python3
"""
app.py  —  Call Number Strip Generator web app
Run with:  python app.py
Then open:  http://localhost:5050
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from flask import Flask, after_this_request, jsonify, render_template, request, send_file

sys.path.insert(0, str(Path(__file__).parent))
from fetch_callnumber_labels import (
    fetch_item,
    extract_row,
    shorten_title,
    generate_barcode,
    update_item_barcode,
)
from make_callnumber_strips import Row, make_pdf

app = Flask(__name__)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


REGIONS = {
    "na": "https://api-na.hosted.exlibrisgroup.com",
    "eu": "https://api-eu.hosted.exlibrisgroup.com",
    "ap": "https://api-ap.hosted.exlibrisgroup.com",
    "ca": "https://api-ca.hosted.exlibrisgroup.com",
    "cn": "https://api-cn.hosted.exlibrisgroup.com",
}

@app.route("/api/config")
def config():
    """Tell the frontend whether a server-side API key / base URL are configured."""
    return jsonify({
        "api_key_configured": bool(os.environ.get("ALMA_API_KEY")),
        "base_url_configured": os.environ.get("ALMA_BASE_URL", ""),
        "regions": REGIONS,
    })


@app.route("/api/fetch", methods=["POST"])
def fetch():
    data       = request.get_json(force=True)
    barcodes   = data.get("barcodes", [])
    api_key    = data.get("api_key") or os.environ.get("ALMA_API_KEY", "")
    base_url   = (data.get("base_url") or os.environ.get("ALMA_BASE_URL")
                  or REGIONS["na"]).rstrip("/")
    mode       = data.get("mode", "existing")   # "existing" | "new"

    if not api_key:
        return jsonify({"error": "No API key — enter one in the settings panel."}), 400
    if not barcodes:
        return jsonify({"error": "No barcodes provided."}), 400

    rows, errors = [], []

    for barcode in barcodes:
        item = fetch_item(barcode, api_key, base_url)
        if item is None:
            errors.append({"barcode": barcode, "error": "Not found"})
            continue

        row = extract_row(item)
        row["short_title"] = shorten_title(item.get("bib_data", {}).get("title", ""))

        if mode == "new":
            new_bc = generate_barcode(api_key, base_url)
            if new_bc is None:
                errors.append({"barcode": barcode, "error": "Could not generate a unique barcode — try again"})
                continue

            # Write the new barcode back to the Alma item record so the
            # catalog stays in sync with the printed strip.  Fail-closed: if
            # the PUT does not succeed, drop the row from the response so we
            # never print a label whose barcode isn't recorded in Alma.
            updated = update_item_barcode(item, new_bc, api_key, base_url)
            if updated is None:
                errors.append({
                    "barcode": barcode,
                    "error": (
                        f"Generated barcode {new_bc} but could not write it "
                        "back to Alma — item left unchanged.  See server log "
                        "for details."
                    ),
                })
                continue

            row["barcode"] = new_bc
        else:
            row["barcode"] = barcode

        if not row["call_number"]:
            errors.append({"barcode": barcode, "error": "No call number in record"})
            continue

        rows.append(row)

    return jsonify({"rows": rows, "errors": errors})


@app.route("/api/inspect", methods=["POST"])
def inspect():
    """Return the raw Alma API JSON for a single barcode — useful for checking
    available fields (e.g. holding_id, item_pid) before building write-back."""
    data     = request.get_json(force=True)
    barcode  = data.get("barcode", "").strip()
    api_key  = data.get("api_key") or os.environ.get("ALMA_API_KEY", "")
    base_url = (data.get("base_url") or os.environ.get("ALMA_BASE_URL")
                or REGIONS["na"]).rstrip("/")

    if not api_key:
        return jsonify({"error": "No API key — enter one in the settings panel."}), 400
    if not barcode:
        return jsonify({"error": "No barcode provided."}), 400

    item = fetch_item(barcode, api_key, base_url)
    if item is None:
        return jsonify({"error": f"Barcode {barcode!r} not found in Alma."}), 404

    return jsonify(item)


@app.route("/api/generate", methods=["POST"])
def generate():
    data           = request.get_json(force=True)
    rows_data      = data.get("rows", [])
    with_barcodes  = bool(data.get("with_barcodes", False))
    strips_per_page = int(data.get("strips_per_page", 4))

    if not rows_data:
        return jsonify({"error": "No rows to generate."}), 400

    rows = [
        Row(
            author      = r.get("author",      ""),
            short_title = r.get("short_title",  ""),
            location    = r.get("location",     ""),
            call_number = r.get("call_number",  ""),
            volume      = r.get("volume",       ""),
            copy_number = r.get("copy_number",  ""),
            barcode     = r.get("barcode",      ""),
        )
        for r in rows_data
    ]

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp_path = tmp.name
    tmp.close()

    @after_this_request
    def cleanup(response):
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return response

    make_pdf(
        rows            = rows,
        out_path        = Path(tmp_path),
        strips_per_page = strips_per_page,
        with_barcodes   = with_barcodes,
    )

    return send_file(
        tmp_path,
        as_attachment = True,
        download_name = "callnumber_strips.pdf",
        mimetype      = "application/pdf",
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"\n  Call Number Strip Generator")
    print(f"  Open http://localhost:{port} in your browser\n")
    app.run(debug=False, port=port)
