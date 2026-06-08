#!/usr/bin/python3.9
"""
index.cgi  —  Minimal CGI entry point for the Flask strip-generator app
on IONOS shared hosting (no mod_passenger).

Design notes:
  - No reliance on os.getcwd() (suexec restricts it on this host).
  - No exec() of activate_this.py; we just append venv site-packages to
    sys.path directly.
  - SCRIPT_FILENAME (set by Apache) is the authoritative path under CGI;
    __file__ is the fallback for shell testing.
"""
import sys
import os

# 1. Locate ourselves.
script_path = os.environ.get("SCRIPT_FILENAME") or __file__
_here = os.path.dirname(script_path) if os.path.isabs(script_path) else \
        "/homepages/15/d578180479/htdocs/samlemley.info/slipscript"

sys.path.insert(0, _here)

# 2. Make the venv's site-packages importable without activate_this.py.
_venv_sp = os.path.join(_here, "venv", "lib", "python3.9", "site-packages")
if os.path.isdir(_venv_sp):
    sys.path.insert(0, _venv_sp)

# 3. Optional .env loader (for ALMA_API_KEY etc.).
_env = os.path.join(_here, ".env")
if os.path.exists(_env):
    try:
        with open(_env) as ef:
            for line in ef:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass

# 4. Run the Flask app via the stdlib CGI handler.
from app import app
from wsgiref.handlers import CGIHandler
CGIHandler().run(app)
