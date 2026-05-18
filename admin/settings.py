from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.environ.get("PGB_ADMIN_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "admin.db"
RUNTIME_DIR = Path(os.environ.get("PGB_RUNTIME_DIR", ROOT / "runtime"))
PGBOUNCER_INI = RUNTIME_DIR / "pgbouncer.ini"
USERLIST_TXT = RUNTIME_DIR / "userlist.txt"

HOST = os.environ.get("PGB_ADMIN_HOST", "0.0.0.0")
PORT = int(os.environ.get("PGB_ADMIN_PORT", "8080"))
SESSION_SECRET = os.environ.get("PGB_ADMIN_SECRET", "change-me-in-production")
SESSION_MAX_AGE = int(os.environ.get("PGB_ADMIN_SESSION_HOURS", "8")) * 3600

PGBOUNCER_LISTEN_PORT = int(os.environ.get("PGBOUNCER_PORT", "6432"))
DOCKER_COMPOSE = os.environ.get("PGB_DOCKER_COMPOSE", "docker compose")
