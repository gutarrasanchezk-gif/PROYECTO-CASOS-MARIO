"""WSGI entry point for cPanel/Passenger and compatible Python hosts."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configuration is read by app.py from the host's environment variables.
# Resolve imports from this file's directory even when Passenger starts elsewhere.
from app import app as application  # noqa: E402
