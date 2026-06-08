# Deployment — IONOS Shared Hosting (Python / Passenger)

## What's in this folder

| File | Purpose |
|---|---|
| `app.py` | Flask web application |
| `fetch_callnumber_labels.py` | Alma API client |
| `make_callnumber_strips.py` | PDF generator |
| `templates/index.html` | Browser UI |
| `requirements.txt` | Python dependencies |
| `passenger_wsgi.py` | WSGI entry point (Passenger reads this) |
| `.htaccess` | Tells Apache to route requests to Passenger |
| `.env` *(you create this)* | Optional server-side secrets |

---

## Step 1 — Enable Python in the IONOS control panel

1. Log in to **IONOS Control Panel → Hosting → your package**
2. Find **Python / App configuration** (sometimes under "Advanced" or "Settings")
3. Enable **Python** and select Python **3.8** or higher
4. Note your **document root** path — usually something like `/homepages/XX/dXXXXXXXXX/htdocs/`

---

## Step 2 — Upload files via SFTP

Connect to your IONOS server with your SFTP client (Cyberduck, FileZilla, etc.) and upload **all files in this folder** to your document root (or a subdirectory if you want the app at a path like `yourdomain.com/callnumbers/`).

Make sure hidden files are visible in your SFTP client — `.htaccess` and `.env` start with a dot and are easy to miss.

Upload this entire directory tree:

```
/                          ← your document root (or subdirectory)
├── app.py
├── fetch_callnumber_labels.py
├── make_callnumber_strips.py
├── passenger_wsgi.py
├── .htaccess
├── requirements.txt
├── .env                   ← optional, see Step 4
└── templates/
    └── index.html
```

---

## Step 3 — Install Python dependencies via SSH

IONOS includes SSH access on most plans.

```bash
# Connect
ssh uXXXXXXXX@access.ionos.com

# Navigate to your app directory
cd /homepages/XX/dXXXXXXXXX/htdocs/

# Create a virtual environment (only needed once)
python3 -m venv venv

# Install dependencies
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

The `passenger_wsgi.py` entry point automatically activates this `venv/` directory.

---

## Step 4 — (Optional) Bake in a server-side API key

If this app is only for your institution and you don't want users to enter an API key in the browser, create a `.env` file in the same directory:

```
ALMA_API_KEY=l8xxYOURKEYHERE
ALMA_BASE_URL=https://api-na.hosted.exlibrisgroup.com
```

When these are set, the browser UI hides the key-entry panel and uses the server key automatically.

**Do not commit or share the `.env` file.**

---

## Step 5 — Restart Passenger

After uploading files or installing packages, touch the restart file:

```bash
mkdir -p tmp && touch tmp/restart.txt
```

Or simply log out and back in to the IONOS Control Panel and toggle Python off/on.

---

## Troubleshooting

**Blank page or 500 error**
Check the Apache error log (usually in `logs/error.log` relative to your home directory, or visible in IONOS Control Panel → Logs).

**`ModuleNotFoundError: No module named 'flask'`**
The venv isn't being found. Double-check that `venv/bin/activate_this.py` exists after running the pip install step. If it doesn't exist, you're using Python ≥ 3.12 (which removed `activate_this.py`) — in that case, edit `passenger_wsgi.py` to point directly to the venv's `site-packages` directory.

**`PassengerEnabled` is not recognised**
Passenger may not be enabled for your plan. Contact IONOS support and ask them to enable "Python / Passenger" for your hosting package.

**Barcodes not scanning / API errors**
Make sure your Alma API key has "Read" access to the Items API. Keys are created in Alma under **Configuration → General → Integration Profiles**.
