# Rare Book Slip Script

A small Flask web app that turns a list of Alma item barcodes into a print-ready PDF of call-number label strips. Built for the rare-book cataloging workflow at Carnegie Mellon University Libraries, but anything running an Ex Libris Alma instance should be able to use it.

## What it does

- Looks up item metadata (author, title, location, call number) in Alma by barcode via the Ex Libris REST API.
- Generates a print-ready PDF of label strips formatted for standard library label stock, four strips per page.
- Optionally mints fresh 14-digit Luhn-valid barcodes for items that have never been barcoded, **and writes the new barcode back to the Alma item record** so the catalog stays in sync with the printed label. If the write-back fails, the row is dropped from the PDF rather than printing a label that the catalog does not recognize.

## Requirements

- Python 3.8 or later
- An Alma API key with **Read/Write** access to the Bibs API (Read-only is enough for the existing-barcode mode but not the new-barcode mode)
- The packages listed in `requirements.txt` (`flask`, `requests`)

## Quick start (local development)

```bash
git clone https://github.com/<your-username>/RareBookSlipScript.git
cd RareBookSlipScript

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and paste in your Alma API key

python app.py
```

Then open <http://localhost:5050> in your browser.

## Usage

1. Paste one or more Alma item barcodes into the input box (one per line).
2. Choose a mode:
   - **Existing barcodes** — print strips carrying the same barcode each item already has. Use this when reprinting damaged labels.
   - **New barcodes** — generate fresh 14-digit barcodes, write them back to Alma, and print strips with the new barcode. Use this for items that have never been barcoded.
3. Click **Fetch** and review the rows and any errors.
4. Click **Generate PDF**.
5. Print double-sided on **short-edge** binding, manual feed, on label stock. The full printing walkthrough lives in `Call_Number_Strip_Generator_Manual.docx`.

There's also a CLI script — `fetch_callnumber_labels.py` — that does the same thing from a `barcodes.txt` file. See `barcodes.example.txt` for the file format.

## Project layout

```
app.py                            # Flask web app
fetch_callnumber_labels.py        # Alma API client + CLI entry point
make_callnumber_strips.py         # PDF generator
templates/index.html              # Browser UI
passenger_wsgi.py                 # WSGI entry point for Passenger hosting
requirements.txt                  # Python dependencies
DEPLOY.md                         # Production deployment notes (IONOS / Passenger)
.env.example                      # Template for required environment variables
barcodes.example.txt              # Template for the CLI input file
```

## Deployment

For shared-hosting / production deployment notes — IONOS with Passenger and a `passenger_wsgi.py` entry point — see `DEPLOY.md`.

## Contributing

Pull requests welcome. If you're adapting this for a different Alma library, the bits most likely to need editing are the `GENERATED_PREFIX` in `fetch_callnumber_labels.py` (the 8-digit prefix used for app-minted barcodes) and the strip layout in `make_callnumber_strips.py`.

## License

[MIT](LICENSE).
