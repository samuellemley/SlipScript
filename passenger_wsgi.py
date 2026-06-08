"""
passenger_wsgi.py  —  WSGI entry point for IONOS / Passenger hosting
"""
import sys
import os

# ── Activate virtual environment (created via SSH: python3 -m venv venv) ──────
_here      = os.path.dirname(os.path.abspath(__file__))
_venv_act  = os.path.join(_here, "venv", "bin", "activate_this.py")

if os.path.exists(_venv_act):
    with open(_venv_act) as _f:
        exec(_f.read(), {"__file__": _venv_act})
else:
    # Fallback: add venv site-packages manually if activate_this is missing
    import glob
    _sp = glob.glob(os.path.join(_here, "venv", "lib", "python3*", "site-packages"))
    if _sp:
        sys.path.insert(0, _sp[0])

sys.path.insert(0, _here)

# ── Optional: load a .env file for server-side secrets ────────────────────────
# Create a file called ".env" alongside this file with lines like:
#   ALMA_API_KEY=l8xxYOURKEYHERE
#   ALMA_BASE_URL=https://api-na.hosted.exlibrisgroup.com
_env_path = os.path.join(_here, ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Import the Flask app as "application" (required by Passenger) ─────────────
from app import app as application  # noqa: E402
